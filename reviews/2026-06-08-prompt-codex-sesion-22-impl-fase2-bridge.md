# Prompt Codex — sesión 22 — implementación fase 2 Sesión A (bridge Enforce, Paso 0-3)

Patrón: implementation handoff (post-R22 approve). Scope acotado al código Enforce del bridge; el harness Python (Paso 4) + in-game son Sesión B. Generado por Claude 2026-06-08. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: implementar el código Enforce del bridge para fase 2 (Observación) de DayZ-MCP. Esta sesión cubre **únicamente los Pasos 0, 1, 2 y 3 del plan v2** (DTOs + helper + validaciones + wiring del dispatch + handlers `scene_raycast` y `telemetry_read`). El **Paso 4 (harness Python: whitelist, --mode phase2, run-fase2.ps1, fixture, suite de verdict) y el spike in-game son Sesión B — NO los implementes aquí ni siquiera parcialmente.** El plan v2 ya pasó R22 (approve, NIT RR22-001 ya corregido en el plan): es spec vinculante.

## Carga inicial obligatoria (lee antes de tocar nada)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-08-fase2-observacion.md
   (plan v2, SPEC vinculante; implementa exactamente Pasos 0-3 + "Semántica ok/error" + "Revisión R22 aplicada").
2. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md
   (contrato verificado; bloque "Observación (fase 2)" + caveats LL-093 / crosshair / layers).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-08-plan-review-fase2-codex.md
   (tu propia R22: confirma los 6 fixes y el cite-then-verify de las firmas [A]).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c
   (DTOs a ampliar — Paso 0).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (dispatch + handlers; moldes: `query_player_state` síncrono :332, `DispatchWorldSpawn` :370, `BuildPlayerState` :1198, `PostResult` :1233, patrón `Insert` :1227-1229, backpressure :3-5).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\3_game\global\dayzphysics.c
   (RaycastRVParams :49, RaycastRVResult :98, RaycastRVProxy :208, RayCastBullet :211, defaults :87-88).

NO releas el research consolidado (ya destilado en el plan). Abre on-demand SOLO para re-verificar (R2) las firmas marcadas `[A]` en el plan que vayas a llamar: `object.c` (GetHealth01/GetPosition/GetOrientation/GetDirection), `entityai.c`/`inventory.c`/`cargo.c` (inventario), `enphysics.c` (GetVelocity), `surfaceinfo.c` (GetName/GetSurfaceType), `dayzplayerimplementmeleecombat.c:671-686` (layer mask melee a copiar VERBATIM). Si una firma difiere del plan, usa la real y anótalo en el handoff.

## Alcance acotado — Pasos 0-3 del plan v2

Archivos de salida (SOLO estos dos):
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c`
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c`

### Paso 0 — DTOs (MCPMessages.c)
- Ampliar `MCPArgs` con `from,to` (`ref array<float>`), `radius`, `method`, `intersect`, `ignore`, `mode`, `path`, `max_lines`; `new` los arrays nuevos en el constructor (como `pos`).
- Ampliar `MCPResult` con `ref MCPRaycastHit raycast;` y `ref MCPTelemetry telemetry;` (patrón del `ref MCPPlayerState state` existente).
- Clases nuevas tal cual el plan: `MCPRaycastHit`, `MCPTelemetry`, `MCPTelemetryFixtureLine` (contrato fijo `{string fixture_id; float value; int seq;}`), `MCPRaycastValidation`, `MCPTelemetryValidation`. Arrays `new` en constructor.
- **Helper `VectorToArray(vector v, out array<float> a)`** (Clear + 3 Insert; patrón `MCPBridge.c:1227-1229`). **Prohibido `a = vector`** (no compila).

### Paso 1 — Wiring (MCPBridge.c)
- Insertar entre `vehicle_drive` (:354-357) y el `else`/`unknown_command` (:358-362): `else if (command.cmd=="scene_raycast"){ postNow=DispatchSceneRaycast(command,result); }` y `else if (command.cmd=="telemetry_read"){ postNow=DispatchTelemetryRead(command,result); }`. Ambos helpers **devuelven `true` siempre** (síncrono; sin `MCPJob`, sin `deadline_s`).

### Paso 2 — `DispatchSceneRaycast` (C1)
- ValidateRaycastArgs (from/to 3 floats, radius>=0 default 0.05, method∈{rvproxy,bullet}, intersect∈allowlist→ObjIntersect). Resolver `ignore` (args.ignore=="player" → GetPlayers()[0] si existe; else null).
- `method=="rvproxy"`: `RaycastRVParams` + `RaycastRVProxy`; **nearest válido por `vector.DistanceSq(from, res[i].pos)`** (NO `res[0]`; patrón `weapon_base.c:1823-1834`). Poblar `MCPRaycastHit` con `VectorToArray` para pos/normal; distance=`vector.Distance(from,r.pos)`; object_type/class, parent_type si hierLevel>0, component, hier_level, surface (si existe), entry/exit.
- `method=="bullet"`: `RayCastBullet` con layer mask vanilla VERBATIM; pos/normal vía `VectorToArray`; distance=`hitFraction*Distance(from,to)`.
- `hit=false` → `result.ok=true` (no error). **Sin `GetCrosshairObject`.**

### Paso 3 — `DispatchTelemetryRead` (C2)
- `mode=="object_at"`: `GetObjectsAtPosition3D` + filtro `GetType()==type`; 0→found=false (ok=true); >1→`ambiguous_fixture` (ok=false); 1→poblar `MCPTelemetry` (pos/orientation/direction/velocity vía `VectorToArray`, class_name, type, health01, inventario acotado, y campos Car de fase 1 si `Car.Cast`).
- `mode=="fixture_jsonl"`: validar `path` empieza por `$mission:dayz_mcp/`; `OpenFile` READ (==0 → `fixture_not_found`, ok=false); bucle `FGets` hasta `max_lines`; parse por línea con `JsonSerializer.ReadFromString`/`JsonFileLoader.LoadData` (NUNCA `LoadFile`); `last_valid`/`line_count_read`/`parse_error`. **NO escribas el fixture** (lo hace run-fase2.ps1 en Sesión B).
- Semántica ok/error EXACTA del plan (§"Semántica ok/error").

## Gate de esta sesión (NO hay test in-game aquí)
1. **AddonBuilder empaqueta `@DayZ_MCP` sin error** (como en fase 1). Si AddonBuilder no está disponible en tu entorno, decláralo y entrega el checklist estático.
2. **Checklist estático** (pega el resultado en Bloque B): (a) cero `= vector` sobre `array<float>` (todo por `VectorToArray`/Insert); (b) sin ternario `?:`; (c) todos los campos nuevos de `MCPArgs`/`MCPResult` declarados y arrays `new` en constructor; (d) las 2 ramas de dispatch devuelven true; (e) nearest por `DistanceSq`, no `res[0]`; (f) sin `GetCrosshairObject`; (g) `Print(string.Format(...))` correcto; (h) sin `new` en bucles de tick.

## Restricciones críticas (vinculantes)
1. **Enforce Script 1.29**: sin ternario `?:` (usa if); `array<float>` por componentes; `Print(string.Format(LOG_TAG + ...))` correcto.
2. **R2 cite-then-verify** de cada firma `[A]` antes de llamarla (lista arriba). Firma que difiera → usa la real + anota.
3. **D-07**: args/result tipados (`ref MCPArgs`/sub-DTOs), NO `map<string,JSONValue>`.
4. **NO scope creep — Paso 4 / Sesión B fuera**: NO toques `mcp_server.py`, `mcp_client.py`, `run-fase1.ps1`; NO crees `run-fase2.ps1` ni `telemetry_fixture.jsonl`; NO añadas comandos a `WHITELISTED_COMMANDS`. Te tentará hacerlo "para poder probar" — RESISTE: el harness es Sesión B. Tampoco refactorices los handlers existentes (spawn/seat/drive) ni `BuildPlayerState` "de paso": solo AÑADE.
5. **NO improvises fuera del plan**. Si algo del plan no encaja (firma distinta, campo que no serializa), R22-corto: anótalo en el handoff con path:line, aplica interpretación conservadora, marca para revisión.
6. **NO te autorrevises (R21) en esta sesión.** Termina Paso 0-3 y para; la doble revisión es sesión separada.

## Output esperado al cerrar (bloques A/B/C/D)
- **Bloque A** — archivos modificados (MCPMessages.c, MCPBridge.c) con tamaño aprox y resumen de qué añadiste.
- **Bloque B** — resultado del gate: output de AddonBuilder (literal) + el checklist estático (a-h) marcado.
- **Bloque C** — hallazgos durante implementación: firmas `[A]` que difirieron del plan, cualquier R22-corto, decisiones conservadoras. Si ninguno: "Sin hallazgos."
- **Bloque D** — handoff para Sesión B: estado al cierre, qué falta (Paso 4 harness + spike in-game), infra lista para reutilizar.

===== PROMPT FIN =====
```
