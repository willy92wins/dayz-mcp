# Prompt Codex — sesión 27 — R21 (revisión de código) fase 2

Patrón: R21 estructural (doble revisión; mitad Codex, detector principal). Claude ya hizo su pasada (receptor read host-direct de los handlers + verificación del verdict in-game). Generado por Claude 2026-06-08. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: R21 (revisión a fondo de código) de las adiciones fase 2 del bridge DayZ-MCP. Esta sesión SOLO revisa y reporta hallazgos por severidad; NO implementes fixes, NO rediseñes, NO toques el harness. fase 2 ya pasó in-game (overall_pass=true), así que busca bugs de correctitud / edge cases que el happy-path NO ejerció.

## Carga inicial obligatoria
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c  (handlers fase-2: Dispatch/Populate/Validate, VectorToArray, IsFiniteFloat).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c  (DTOs fase-2).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md  (spec v2 post-R22 — el contrato que el código debe cumplir).
4. C:\tmp\fase2-verdict.json  (qué se validó in-game; overall_pass=true).

NO revises el código fase-0/1 (ya revisado) ni el harness (run-fase2.ps1/mcp_*.py). Alcance = SOLO el diff fase-2.

## Alcance (símbolos a revisar)
`DispatchSceneRaycast`, `PopulateRaycastRVProxy`, `PopulateRaycastBullet`, `DispatchTelemetryRead`, `DispatchTelemetryObjectAt`, `DispatchTelemetryFixtureJsonl`, `PopulateTelemetryObject`, `PopulateTelemetryInventory`, `ValidateRaycastArgs`, `ValidateTelemetryArgs`, `VectorToArray`, `IsFiniteFloat`, el wiring del dispatch, y los DTOs nuevos de MCPMessages.c.

## Dimensiones de la revisión
1. **Correctitud raycast**: selección nearest por `DistanceSq` (¿maneja results vacío / todos null / bestIndex<0?); cálculo de `distance`; `hit=false`→`ok=true`; `ignore` resolución; `intersect` allowlist→ObjIntersect.
2. **Caminos NO ejercitados in-game** (foco — el verdict no los cubrió): `C1_proxy_parent` salió `evaluated=0` (hier_level>0 nunca ocurrió) → revisar la rama proxy/parent; `surface_name` salió "" en los hits → revisar la extracción `SurfaceInfo.GetName/GetSurfaceType` (¿null-safe? ¿lifetime del handle?).
3. **Parser fixture_jsonl**: bucle `FGets` (EOF vs línea vacía, `max_lines`, líneas largas), `CloseFile` en TODAS las salidas (incl. error), `parse_error` propagación, allowlist de path fail-closed.
4. **telemetry object_at**: enumeración `GetObjectsAtPosition3D` + filtro tipo; `>1`→ambiguous; inventario acotado por `TELEMETRY_ITEMS_CAP` (¿off-by-one? ¿null items?); `Car.Cast`/`EntityAI.Cast` null-safety; `GetHealth01` server-auth.
5. **Semántica ok/error**: consistente en TODOS los casos (bad_args/ambiguous/fixture_not_found/parse_error → ok=false; found/hit=false → ok=true). ¿Algún caso sin clasificar?
6. **Enforce/memoria**: refs/managed de los DTOs y arrays (¿leaks? ¿null en serialización JSON de sub-DTOs?); `VectorToArray` con array no inicializado; sin `new` en el camino caliente del tick.
7. **R7 invariantes**: ¿algún call-site o invariante del bridge que las adiciones rompan (DTO compartido, dispatch, backpressure 4/tick que ahora cuenta read-only)?
8. **Server-auth**: cada getter de telemetry, ¿válido en MissionServer? (flag si alguno es client-cached).

## Criterio de severidad
- **FAIL**: bug de correctitud / crash / exception / dato falseado / camino que rompe el contrato.
- **WARN**: edge case no manejado, retrabajo probable, camino sin test que parece frágil.
- **NIT**: claridad/estilo/mejora opcional.

## Output esperado
Archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-08-r21-fase2-codex.md
- Resumen ejecutivo (veredicto: clean / minor / needs-fix).
- Matriz de hallazgos: | ID | Símbolo/línea | Severidad | Resumen | Resolución sugerida |.
- Detalle por hallazgo con cita `path:line` (R2 cite-then-verify de cada API que cuestiones).
Al cerrar, cross-link con timestamp + status=open en `DayZ_MCP_dev/reviews/codex-review-inbox.md`.

===== PROMPT FIN =====
```
