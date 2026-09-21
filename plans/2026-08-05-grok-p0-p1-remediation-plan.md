# Plan de remediación — arbitraje Grok P0/P1

**Fecha:** 2026-08-05  
**Research:** `AI/10_Projects/DayZ_MCP/research/2026-08-05-grok-review-restore-gameplay-codex.md`  
**Estado:** restore/BUG-040 autorizado; resto `PLAN_ONLY / GATED`  
**Restricciones vigentes:** D-33 plataforma congelada; `vehicle_trace` live/deploy
prohibido hasta adjudicación; no interferir con runs ajenos

## Conclusión del arbitraje

Grok encontró trabajo útil, pero infló severidades y mezcló deuda ya cubierta
con un falso positivo. Se aceptan cinco hallazgos con correcciones de solución,
seis ya están cubiertos por artifacts previos y se rechaza el hallazgo de
captura negra porque el código vivo ya lo detecta.

| Hallazgo Grok | Veredicto Codex | Severidad concreta | Acción |
|---|---|---|---|
| `build:true` roto | aceptado, severidad rebajada | degradación operativa HIGH; falla explícitamente, no falso verde/P0 | diagnóstico tras levantar D-33 |
| `except Exception` genérico | aceptado parcialmente | degradación de diagnóstico | códigos tipados para fallos esperados + log sanitizado; no exponer `str(exc)` |
| daemon cachea whitelist | ya cubierto | restricción operativa normal de proceso long-lived | stamp de rollout; no hot reload |
| BUG-066(c) | aceptado | degradación/corrupción de medición | cambiar a APIs attachment correctas + gate |
| sin `restore_gameplay` | aceptado | degradación de usabilidad P1 | implementar ahora |
| BUG-065 | ya cubierto, sigue abierto | degradación de cobertura/readiness P1 | pilotar contrato existente antes de productizar |
| BUG-061 | ya cubierto, sigue abierto | degradación de viabilidad HIGH/GATE | ejecutar sólo plan R22/R26 existente cuando se autorice |
| BUG-040 | aceptado | degradación latente P1 | implementar ahora |
| `players:[]` ambiguo | observación válida, P1 rechazado | semántica/cosmético P3 | sin cambio urgente; el nombre de tool da el tipo de resultado |
| coords motor vs log | ya cubierto | hazard operativo documentado | conservar SP-060; no cambiar wire |
| captura con display dormido | rechazado-falso | no aplica al código vivo | ya existe fail-closed `frame_client_all_black` + test |
| BUG-009 | ya cubierto; vigencia no demostrada | degradación histórica/unknown | reproducir stack actual antes de diseñar fix |

## Gate DPF

| Fase | Criterio/Intent | Estado |
|---|---|---|
| Restore + BUG-040 | G4 nuevo; sirve Intent D y G | autorizado source-only |
| BUG-066(c) | C2/Intent C: telemetría fiable del fixture | previamente autorizado por D-33; no ejecutar en esta sesión |
| Build/errores/rollout | E3 + Intent E: instalación/build operable | bloqueado hasta levantar D-33 |
| Mission readiness | H/Intent coordinación y cobertura de runs; falta criterio explícito | product-spec debe completarse antes de cambiar API |
| BUG-061 | G3 | usar plan existente; live requiere autorización |
| BUG-009 | E3/H, sólo tras reproducción actual | discovery primero |

## Fase 0 — research y arbitraje

Estado: **COMPLETADA**.

1. Contrastar cada fila con código, memoria, planes y evidencia in-game.
2. Separar bug vivo, prior art, limitación operativa y falso positivo.
3. No convertir severidad reportada por Grok en hecho sin reproducibilidad.

Salida: research citado y tabla anterior.

## Fase 1 — `restore_gameplay` + BUG-040

Estado: **AUTORIZADA PARA SOURCE-ONLY**.

Ejecutar exactamente `plans/2026-08-05-restore-gameplay-feature-spec.md` con TDD.
Gate terminal de esta sesión: tests de contrato + suites relacionadas verdes,
sin deploy ni lifecycle. PACKONLY e in-game quedan pendientes por existir un run
ajeno y porque no se autorizó interferir con él.

## Fase 2 — BUG-066(c), lote PBO mínimo

Estado: **PLAN READY, NO EJECUTAR AHORA**.

1. Reabrir las cuatro firmas vanilla antes del diff:
   `P:/scripts/3_game/systems/inventory/inventory.c:167-184`.
2. Escribir caracterización RED que exige nombres de todos los attachment slots
   declarados y rechaza `""`.
3. `[EXACT]` Sustituir sólo `GetSlotIdCount()`/`GetSlotId(i)` por
   `GetAttachmentSlotsCount()`/`GetAttachmentSlotId(i)` en
   `DayZ_MCP/scripts/5_Mission/MCPBridge.c:1739-1744`.
4. Compilar/PACKONLY dos veces desde staging, inventariar y comparar contenido.
5. Agrupar en un único PBO el source final de Fase 1 + este fix; no añadir
   instrumentación BUG-061 al lote sin su autorización separada.
6. Gate in-game: fixture con slots declarados conocidos, nombres no vacíos,
   regresión de telemetría, restore y get-in; rollback por hash.

Exit: medición exacta, PBO atribuible, cero regresión y cleanup limpio.

## Fase 3 — `build:true`, errores tipados y stamp de rollout

Estado: **BLOQUEADA POR D-33; requiere autorización explícita**.

### 3A — observabilidad segura antes de reproducir

1. Añadir tests negativos que demuestren que `DayzTestToolError.code` y
   `ToolError` se preservan y una excepción inesperada no filtra path, key ni
   texto arbitrario.
2. `[DESIGN]` Añadir correlación interna y log sanitizado con clase de excepción,
   etapa y run id; respuesta pública estable `dayz_test_failed` para lo
   inesperado.
3. Convertir únicamente fallos lower-layer esperados y demostrados en códigos
   `DayzTestToolError` estables. No inventar catálogo antes del stack causal.

### 3B — reproducción controlada

1. Esperar caja limpia, adquirir lease y sellar hash/mtime del PBO previo.
2. Ejecutar una sola reproducción `build:true` en fixture aislado con el nuevo
   correlador; capturar stack sanitizado y artifacts.
3. Comparar contra AddonBuilder manual con exactamente las mismas roots/args.
4. Aplicar el fix causal mínimo y repetir RED→GREEN.

Exit: `build:true` retorna envelope tipado, escribe PBO sólo en staging esperado,
hash cambia de forma atribuible y una falla sintética no fuga secretos.

### 3C — cache/rollout

1. No implementar hot reload.
2. `[DESIGN]` Exponer en `bridge_status` un stamp no sensible de generación de
   daemon + digest de whitelist/source bundle.
3. Gate de rollout: source Python, daemon cargado y PBO deben acreditar el mismo
   stamp antes de usar un verbo nuevo; restart controlado si no coincide.

## Fase 4 — BUG-065 Mission readiness

Estado: **DISCOVERY/PILOT, no implementación global autorizada**.

1. Ejecutar primero el piloto externo ya auditado de LFPowerGrid, sin modificar
   DayZ_MCP, sólo cuando el usuario apruebe su ventana/run.
2. Validar que la señal `Module: Mission` es estable, run-scoped, atribuible y
   termina siempre con stop exact-once.
3. Añadir al product-spec de DayZ_MCP un criterio explícito para la semántica de
   éxito de `mode="all"` antes de cambiar su API/latencia.
4. Decisión de producto requerida:
   - recomendación: `mode="all"` no declara success hasta Mission cliente;
   - compatibilidad: documentar el aumento de latencia y timeout separado;
   - fallo: código tipado, cleanup exact-once y artifact run-scoped.
5. Adaptar los 19 viability tests del piloto al paquete DayZ_MCP, no copiar
   paths ni supuestos LFPowerGrid.

Exit: baseline y negativo deterministas, Mission acreditada o error tipado, run
terminal y sin procesos huérfanos.

## Fase 5 — BUG-061

Estado: **NO DUPLICAR PLAN; congelado hasta autorización**.

Ejecutar `plans/2026-07-26-vehicle-trace-cadence-oncontact-r22-r26.md` sólo desde
Fase A0 a A4. No repetir live ni modificar hooks antes de esa caracterización.
Si `OnContact` resulta server-only, STOP y decisión humana de arquitectura; no
añadir RPC/SyncVar implícitamente.

## Fase 6 — BUG-009

Estado: **DISCOVERY**.

1. Definir una matriz actual de al menos cinco `mode="all"` con la misma caja,
   timeout y fixture, una vez cerrada BUG-065 para no mezclar readiness con
   autoconexión.
2. Clasificar por etapa exacta: launcher ACK, proceso cliente, Game, World,
   Mission, player spawn.
3. Si no reproduce, cerrar/superseder BUG-009 con evidencia; si reproduce,
   abrir un plan causal separado. No tratar `server_wait` mayor como fix.

## Hallazgos sin fase de código

- `players:[]`: mantener sin cambio de schema. Si aparece un consumidor que
  pierde el nombre de tool, resolver en su envelope tipado, no añadir campos a
  todos los resultados por anticipado.
- Coords: mantener wire motor `[x,y,z]` y la conversión SP-060 del log; cambiar
  el wire rompería compatibilidad sin aportar valor.
- Captura: cerrar como falso positivo; conservar su test de regresión.

## Orden recomendado

1. Fase 1 ahora.
2. Fase 2 en el próximo gate PBO autorizado.
3. Pedir decisión para levantar D-33; si se levanta, Fase 3 antes de más builds.
4. Pilotar Fase 4 antes de productizar readiness.
5. Fase 5 aislada y después Fase 6, para no confundir fallos de instrumentación,
   Mission y autoconexión.

## Validación y handoff

- Cada fase conserva sus RED/negativos y falla cerrada ante evidencia ambigua.
- Todo trabajo Enforce exige checklist DayZ, PACKONLY y gate in-game separado.
- No matar procesos DayZ; lease JIT y lifecycle guard.
- El árbol Git es inválido: usar manifest, SHA-256 y handoff durable en lugar de
  un commit ficticio.
