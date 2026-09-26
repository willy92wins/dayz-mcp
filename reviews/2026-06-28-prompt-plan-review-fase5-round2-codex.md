# Prompt R22 round-2 (cierre Codex) — Fase 5 drivability v2

> Generado 2026-06-28 (Claude builder). Round-2 tras NEEDS-WORK: verificación de CIERRE, no
> re-review completa. Pegar de marcador a marcador en Codex CLI.

```
===== PROMPT INICIO =====

Tarea: R22 round-2 (VERIFICACIÓN DE CIERRE) del plan Fase 5 de DayZ-MCP, v2. El round-1 fue
NEEDS-WORK con 9 hallazgos (F5-001..009); el receptor (Claude) los aplicó a v2. NO es una review
nueva: verificas que cada hallazgo quedó CERRADO en v2 y cazas regresiones introducidas por las
ediciones. NO implementes, NO reescribas. R22 de tu AGENTS.md aplica.

CARGA INICIAL OBLIGATORIA

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-drivability-autonoma.md
   (plan v2 — el artefacto a verificar; §12 trae el changelog de resoluciones F5-001..009).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-28-plan-review-fase5-codex.md
   (TU review round-1 con la matriz F5-001..009 — la fuente de verdad de qué había que cerrar;
   VERIFICA las resoluciones, no asumas que el changelog dice la verdad).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (grupo G G0-G2 añadido + changelog 2026-06-28 + cross-ref B3; verifica el cierre de F5-001 y
   la trazabilidad bidireccional plan↔grupo G).
4. Para las CITAS NUEVAS de F5-006, spot-check en
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\3_game\entities\pawn.c
   (IsOwner ~:194, GetOwnerIdentity ~:209) y
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\3_game\entities\object.c (GetNetworkID ~:815).
5. Solo si necesitas re-confirmar un ancla del bridge que el v2 cite:
   C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\ (MCPClientBridge.c,
   MCPMessages.c, MCPBridge.c).

NO releas el architecture ni el plan F4 salvo que una resolución del v2 los contradiga.

QUÉ VERIFICAR (cierre, no rediseño)

A) **Por cada F5-001..009**: dictamen `closed` / `partial` / `open`, con CITA de la sección NUEVA
   del v2 (§/línea) que lo resuelve. No basta que §12 lo afirme — confírmalo en el cuerpo del plan.
   En particular:
   - F5-002: ¿`component` es realmente obligatorio para PASS y sin él el result es `partial` y
     nunca `available==true`? (§4.1 + Gate B.4)
   - F5-003: ¿`vehicle_release` está en la surface (§3) Y hay deadman/TTL con auto-release (§3.1) Y
     gate (A.6/A.7)? ¿El deadman cubre el caso "daemon sobrevive a la sesión" (LL-156)?
   - F5-006: ¿S0 reporta IsOwner/GetOwnerIdentity/net id y las 3 firmas existen donde el v2 dice?
   - F5-008: ¿el fixture de Gate B.2 garantiza componentNN presente + asiento mapeado ANTES de
     esperar `crew_can_get_through` como first_block?
   - F5-009: ¿la escalera (§6) restaura gameplay antes de get-in/drive y prohíbe freecam?
B) **Regresiones (R22b)**: ¿alguna edición de v2 introdujo una inconsistencia nueva (etiqueta
   stale, número de sección roto, contradicción entre §3.1 y §3.2, DTO citado que no existe)?
   Cada una = id nuevo `R22bF5-NNN`.
C) **Trazabilidad bidireccional**: grupo G del product-spec (G0/G1/G2) ↔ gates S0/A/B del plan ↔
   §12. ¿El mapeo es completo en ambos sentidos? ¿G2 "component obligatorio" casa con §4.1?
D) **F5-001 cierre**: ¿el grupo G está en product-spec ANTES de S0 (no "al cierre") y el plan §1/§10
   ya no propone el criterio como pendiente?

CRITERIO DE SEVERIDAD: igual que round-1 (FAIL bloqueante / WARN mayor / NIT). Una resolución
`open` o `partial` de un P1 = bloqueante para empezar S0.

OUTPUT ESPERADO

Archivo único:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-28-plan-review-fase5-round2-codex.md

Estructura:
### Resumen ejecutivo
- Veredicto nuevo: approve / approve with minor changes / reject.
- Si no es approve: lista EXACTA de lo que falta cerrar.

### Tabla de cierre
| ID | Dictamen (closed/partial/open) | Sección v2 que lo cierra | Nota |
|---|---|---|---|
| F5-001 | closed | §1, §10, product-spec §G | ... |
| ... (los 9) |

### Regresiones nuevas (si las hay)
| ID | Sección v2 | Sev | Problema | Fix |
| R22bF5-001 | ... |

### Citas F5-006 spot-checkeadas
IsOwner / GetOwnerIdentity / GetNetworkID: confirmadas / divergen (con línea real).

### Próximo paso
- approve → implementación de S0 puede empezar.
- si no → lo que Claude debe cerrar.

Al terminar, append con timestamp + status al canal:
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md

===== PROMPT FIN =====
```
