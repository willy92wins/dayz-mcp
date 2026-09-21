# R22 round-2 plan-review - Fase 5 drivability autonoma (Codex)

> Recibido 2026-06-28. Verificacion de cierre, no review nueva. Scope: comprobar v2 contra
> `reviews/2026-06-28-plan-review-fase5-codex.md` (F5-001..009), `product-spec.md` grupo G,
> y spot-check R2 de las firmas nuevas F5-006.

### Resumen ejecutivo

- Veredicto nuevo: **approve with minor changes**.
- Cierre round-1: **8 closed, 1 partial**. No queda ningun P1 open/partial; S0 no esta bloqueado por los hallazgos originales.
- Falta cerrar antes de dar por limpio el plan v2:
  - `R22bF5-001` (WARN/P2): Gate A.1 usa `engine_on`, pero v2 cerro no-renombrar y mantener `engine_on_server`.
  - `R22bF5-002` (NIT/P3): titulo stale "4 verbos modulares" cuando la surface ya son 5 verbos con `vehicle_release`.
  - `R22bF5-003` (NIT/P3): `hold_ttl_s` aparece en DTO/held-state, pero falta en la columna Args de `vehicle_control`.

### Tabla de cierre

| ID | Dictamen (closed/partial/open) | Seccion v2 que lo cierra | Nota |
|---|---|---|---|
| F5-001 | closed | plan v2 §1:62-67, §10:314-319; product-spec §G:102-117, changelog:161-168 | DPF cerrado antes de S0. B3 cross-ref apunta a grupo G en product-spec:55. |
| F5-002 | closed | plan v2 §4.1:179-193; Gate B.4:277 | `component` es obligatorio para PASS; sin `component`, `partial=true` y nunca `available==true`. |
| F5-003 | closed | plan v2 §3 tabla:115-122; §3.1:136-140; Gate A.6/A.7:264-265; §5:216-218 | `vehicle_release` esta en surface, hay deadman/TTL y cubre daemon sobreviviendo a la sesion (LL-156). |
| F5-004 | closed | plan v2 §3.1:130-135; Gate A.8:266 | Identidad por net id fijada al activar; auto-clear si cambia el coche o sale del vehiculo. |
| F5-005 | partial | plan v2 §3.2:144-152 | La decision principal esta cerrada (no rename en F5), pero Gate A.1 contradice usando `engine_on==true` en vez de `engine_on_server==true` (ver R22bF5-001). |
| F5-006 | closed | plan v2 §2:85-96; Gate S0:253-256; §11:334 | S0 reporta `IsOwner()`/`GetOwnerIdentity()`/net id y usa esos datos para clasificar FAIL. Firmas spot-checkeadas abajo. |
| F5-007 | closed | plan v2 §5:208-213; Gate A.9:267 | Rango + NaN/Inf estan gateados para throttle/steer/brake/handbrake/gear; referencia `IsFiniteFloat` confirmada en bridge. |
| F5-008 | closed | plan v2 Gate B.2:271-274 | Fixture SUB_BRZ ahora exige componentNN presente + asiento mapeado antes de esperar `crew_can_get_through`. |
| F5-009 | closed | plan v2 §6:228-242; riesgo §8:290 | Escalera anade R2.5 restore-gameplay antes de get-in/drive y prohibe freecam en ese tramo. |

### Regresiones nuevas

| ID | Seccion v2 | Sev | Problema | Fix |
|---|---|---|---|---|
| R22bF5-001 | Gate A.1:259 vs §3.2:149-152 | WARN/P2 | El gate dice `engine_set start -> engine_on==true`, pero v2 decidio no renombrar `engine_on_server`; `MCPResult` actual tiene `engine_on_server` en `MCPMessages.c:214`. | Cambiar Gate A.1 a `engine_on_server==true` / `false`, o explicitar que `engine_on` es solo alias de lectura y no campo wire. |
| R22bF5-002 | §3.3 titulo:154 vs verbos:155 | NIT/P3 | El titulo sigue diciendo "4 verbos modulares" aunque la lista ya incluye `vehicle_release` como quinto verbo. | Renombrar el titulo a "verbos modulares vs reusar `drive_probe`" o "5 verbos modulares". |
| R22bF5-003 | §3 tabla:118 vs §3.2:145-146 | NIT/P3 | `MCPArgs` anade `hold_ttl_s`, pero la fila `vehicle_control` no lo lista como arg opcional. | Anadir `hold_ttl_s?` a la columna Args de `vehicle_control`, o retirar el campo si el TTL sera constante interna. |

### Citas F5-006 spot-checkeadas

- `IsOwner`: confirmada en `scripts/3_game/entities/pawn.c:194` como `proto native bool IsOwner();`.
- `GetOwnerIdentity`: confirmada en `scripts/3_game/entities/pawn.c:209` como `proto native PlayerIdentity GetOwnerIdentity();`.
- `GetNetworkID`: confirmada en `scripts/3_game/entities/object.c:815` como `proto void GetNetworkID(out int lowBits, out int highBits);`.

### Trazabilidad bidireccional

- product-spec G0:112 -> plan Gate S0:253-256 y §2:85-96. Completo.
- product-spec G1:113 -> plan §3:115-122, §3.1:123-142, Gate A:258-267. Completo salvo las dos limpiezas doc-only R22bF5-001/R22bF5-003.
- product-spec G2:114 -> plan §4.1:179-193 y Gate B:269-277. Completo; `component` obligatorio casa con §4.1 y Gate B.4.
- plan §10:314-319 -> product-spec §G:102-117. Completo; ya no propone E5 "al cierre".
- plan §12:344-352 -> cuerpo del plan: ver tabla de cierre. No detecte resolucion afirmada solo en changelog sin cuerpo.

### Proximo paso

Claude debe aplicar los 3 ajustes documentales `R22bF5-001..003`. No hay P1 abierto; tras esos ajustes, la implementacion de S0 puede empezar con scope limitado a `drive_probe_client` + reporte de ownership.
