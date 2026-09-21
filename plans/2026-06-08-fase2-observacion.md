# Plan — Fase 2 (Observación): scene_raycast + telemetry_read  (v2, post-R22)

> Spec para implementación por Codex (paso 3). **v2 incorpora la revisión R22 de Codex (2026-06-08)** — ver §"Revisión R22 aplicada".
> Research consolidado: `AI/10_Projects/DayZ_MCP/research/2026-06-08-fase2-observacion.md`.
> Etiquetas: **[EXACT]** = verificado host-direct contra código real · **[DESIGN]** = pseudocódigo a implementar.
> Traza a product-spec grupo C: **C1** (`scene_raycast` hit estructurado obj/dist/normal) y **C2** (`telemetry_read` parse de fixture/JSON-lines). Intent C: "verdicts de test headless sin captura visual".

## Decisiones fijadas (Grill Modo B, 2026-06-08)
1. **Alcance**: C1 + C2 juntas (un ciclo de test in-game, R5).
2. **CONFLICT-1 (API raycast)**: el **spike mide ambas** (`RaycastRVProxy` y `RayCastBullet`) contra el mismo `from/to`; la primaria se elige por el dato y se registra en decision-log + HANDOFF.
3. **Target del spike C1**: **dinámico + estático** (vehículo spawneado Y muro/edificio a distancia conocida).
4. **Cláusula de desafío (architecture §6)**: **drop de `GetCrosshairObject`** (cliente-only) ratificado. `scene_raycast` usa `from`/`to` explícitos. Anotar en `product-spec.md` (Changelog) y `architecture` §6.

## Revisión R22 aplicada (Codex 2026-06-08; verificado host-direct por Claude)
- **P1-1 [EXACT]**: `array<float>` NO admite asignar `vector` directamente; el bridge rellena por componentes con `Insert` (`MCPBridge.c:1227-1229`). → **helper `VectorToArray`** obligatorio (Paso 0); todo `pos/normal/orientation/direction/velocity` lo usa.
- **P1-2 [EXACT]**: `RaycastRVProxy` **no garantiza orden** (literal `weapon_base.c:1815`); vanilla elige el más cercano por `DistanceSq` (`:1823-1834`). → C1 **itera y elige el nearest válido**, NUNCA `results[0]`.
- **P1-3 [EXACT]**: el harness Python rechaza las tools nuevas — `WHITELISTED_COMMANDS` solo fase 0/1 (`mcp_server.py:14`, rechazo `:116-117`); cliente solo `poc|phase1` (`mcp_client.py:629`). → **Paso 4** (whitelist + `--mode phase2` + verdict + `run-fase2.ps1`).
- **P1-4**: fixture JSON-lines estaba como ejemplo, sin productor/ruta. → **contrato fijo** (Paso 3): path, JSONL exacto, DTO, expected, y **productor = `run-fase2.ps1`** (no el bridge; anti-tautología LL-115).
- **P2-1**: semántica `ok`/`error` cerrada (§"Semántica ok/error").
- **P2-2**: `ignore` definido (Paso 2): opcional, `"player"`→cliente conectado, default `null`.

## Invariantes heredadas (NO re-decidir)
- Read-only síncrono en `Dispatch`, patrón `query_player_state`: **sin pending-jobs, sin deadline**. **[EXACT]** `MCPBridge.c:332-345`, `:364-367`.
- **Raycast headless necesita cliente conectado** (LL-093, `lessons-learned.md:1621`): server-only-sin-cliente → hit=0. Radius fino (~0.05); offset LOCAL vía `ModelToWorld` si el target spawnea con pitch/roll.
- Backpressure reutilizable, read-only **comparte** budget. **[EXACT]** `MAX_DISPATCH_PER_TICK=4` `MCPBridge.c:3`, `MAX_PENDING=32` `:4`, `bridge_queue_full` `:272-274`, `MAX_QUEUE=64` `mcp_server.py:15`.
- DTOs/result tipados (D-07), serializados con `JsonSerializer.WriteToString` **[EXACT]** `MCPBridge.c:1240-1242`.

---

## Paso 0 — Contrato de mensajes (DTOs)

**[EXACT] Estado actual** (`MCPMessages.c`): `MCPArgs {string type; ref array<float> pos; int flags,rotation,seat; float throttle,duration;}` (`:8-22`); `MCPResult` con sub-DTO `ref MCPPlayerState state` (`:57`); validación `MCPSpawnValidation {bool ok; string error; vector pos; int flags,rotation;}` (`:98-105`). **Los `array<float>` se rellenan SIEMPRE por componentes** (patrón `BuildPlayerState` `MCPBridge.c:1227-1229`).

**[DESIGN] Helper obligatorio** (P1-1): `void VectorToArray(vector v, out array<float> a) { a.Clear(); a.Insert(v[0]); a.Insert(v[1]); a.Insert(v[2]); }`. **Prohibido `a = v`** (no compila: `vector` ≠ `array<float>`). Aplica a `pos/normal/orientation/direction/velocity`.

**[DESIGN] Ampliar `MCPArgs`** (los arrays se `new` en el constructor, como `pos`):
```
ref array<float> from, to;   // C1 origen/fin del rayo
float radius;                // C1 (default 0.05, LL-093) / C2 object_at search radius
string method;               // C1: "rvproxy"(default) | "bullet"
string intersect;            // C1 rvproxy: "view"(default)|"fire"|"geom"|"ifire"
string ignore;               // C1 opcional: "player"(ignora el cliente) | ""(null)
string mode;                 // C2: "object_at" | "fixture_jsonl"
string path;                 // C2 fixture_jsonl: ruta allowlist "$mission:dayz_mcp/..."
int max_lines;               // C2 fixture_jsonl: tope (default 64)
```

**[DESIGN] Ampliar `MCPResult`** (sub-DTOs, patrón `ref MCPPlayerState state`): `ref MCPRaycastHit raycast;` `ref MCPTelemetry telemetry;`.

**[DESIGN] Clases nuevas** (arrays `new` en constructor):
```
class MCPRaycastHit { bool hit; string method; ref array<float> pos, normal; float distance;
  string object_type, object_class, parent_type; int component, hier_level;
  string surface_name, surface_type; bool entry, exit; }

class MCPTelemetry { string mode; bool found;
  // object_at: arrays rellenados con VectorToArray
  string type, class_name; ref array<float> pos, orientation, direction, velocity; float health01;
  int attachment_count, cargo_count; ref array<string> items;   // cap (p.ej. 16)
  bool engine_on_server; float speedo; int wheel_count; float fuel_fraction;   // si Car (reusa fase 1)
  // fixture_jsonl:
  string path; int line_count_read; ref MCPTelemetryFixtureLine last_valid; string parse_error; }

class MCPTelemetryFixtureLine { string fixture_id; float value; int seq; }   // CONTRATO fijo (no ejemplo), ver Paso 3

class MCPRaycastValidation   { bool ok; string error; vector from, to; int intersect_type; float radius; }
class MCPTelemetryValidation { bool ok; string error; string mode, type, path; vector pos; float radius; int max_lines; }
```

## Semántica ok/error (P2-1, cerrada)
- **`ok=false`** (error de protocolo/validación): `bad_args`, `ambiguous_fixture`, `fixture_not_found`, `parse_error`.
- **`ok=true`** (resultado válido de escena): entidad no encontrada → `found=false`; raycast sin impacto → `hit=false`. **Nunca** error en estos dos.

---

## Paso 1 — Wiring del dispatch
**[EXACT]** (`MCPBridge.c`): entre `vehicle_drive` (`:354-357`) y el `else`/`unknown_command` (`:358-362`):
```
else if (command.cmd == "scene_raycast")  { postNow = DispatchSceneRaycast(command, result); }
else if (command.cmd == "telemetry_read") { postNow = DispatchTelemetryRead(command, result); }
```
Ambos helpers **devuelven `true` siempre** (read-only síncrono → PostResult en el mismo tick; NO `MCPJob`, NO `deadline_s`). Molde de helper: `DispatchWorldSpawn` **[EXACT]** `:370-381`.

## Paso 2 — C1 `scene_raycast` (mide ambas APIs, nearest válido)
**[DESIGN] `DispatchSceneRaycast(command,result)`**:
1. `ValidateRaycastArgs`: exige `from`/`to` (3 floats c/u), `radius>=0` (default 0.05), `method`∈{rvproxy,bullet} (default rvproxy), `intersect`∈allowlist→`ObjIntersect` (default `ObjIntersectView`). Fuera de allowlist → `result.ok=false; result.error="bad_args"; return true`.
2. **Resolver `ignore`** (P2-2): `Object ignore=null; if(args.ignore=="player"){ array<Man> ps=new array<Man>(); GetGame().GetPlayers(ps); if(ps.Count()>0) ignore=ps.Get(0); }`. El rayo es `from/to` explícito (no player-bound) → `ignore` opcional, default null; **no** `no_players` (la presencia de cliente la garantiza el harness, LL-093, pero no la valida esta tool).
3. `MCPRaycastHit hit = new MCPRaycastHit(); hit.method=method;`
4. **`method=="rvproxy"`**:
   - **[EXACT]** `RaycastRVParams p = new RaycastRVParams(from, to, ignore, radius)` (`dayzphysics.c:78`); `p.flags` conserva `CollisionFlags.NEARESTCONTACT`, `p.type = intersect_type` (default del constructor `type=ObjIntersectView`; `dayzphysics.c:87-88`).
   - **[EXACT]** `array<ref RaycastRVResult> res = {}; bool ok = DayZPhysics.RaycastRVProxy(p, res)` (`dayzphysics.c:208`).
   - **Nearest válido (P1-2, NO `res[0]`)** — patrón `weapon_base.c:1815-1834` **[EXACT]**: `int bi=-1; float best=float.MAX; for(int i=0;i<res.Count();i++){ float d=vector.DistanceSq(from,res[i].pos); if(d<best){best=d;bi=i;} } ` → `r = res[bi]`.
   - Si `ok && bi>=0`: `hit.hit=true`; `VectorToArray(r.pos,hit.pos)`; `VectorToArray(r.dir,hit.normal)` (**`dir`="direction outside", no normal unitaria garantizada** `:104`); `hit.distance=vector.Distance(from,r.pos)`; si `r.obj`: `hit.object_type=r.obj.GetType()`, `hit.object_class=r.obj.ClassName()`; si `r.hierLevel>0 && r.parent`: `hit.parent_type=r.parent.GetType()`; `hit.component=r.component`; `hit.hier_level=r.hierLevel`; si `r.surface`: `hit.surface_name=r.surface.GetName()`, `hit.surface_type=r.surface.GetSurfaceType()` (`surfaceinfo.c:24+` **[A], re-verificar en impl**; NO almacenar el handle); `hit.entry=r.entry`; `hit.exit=r.exit`.
5. **`method=="bullet"`** (comparación; normal nativa):
   - **[EXACT]** `DayZPhysics.RayCastBullet(from, to, layerMask, ignore, hitObject, hitPosition, hitNormal, hitFraction)` (`dayzphysics.c:211`).
   - `layerMask`: **reusar un mask vanilla VERBATIM** (p.ej. obstrucción melee `dayzplayerimplementmeleecombat.c:671-686` **[A], leer host-direct en impl**) — NO hand-rollear (`PhxInteractionLayers` ordinales, `dayzphysics.c:1-43`).
   - `hit.hit=ok`; `VectorToArray(hitPosition,hit.pos)`; `VectorToArray(hitNormal,hit.normal)` (**fiable**); `hit.distance=hitFraction*vector.Distance(from,to)`; si `hitObject`: `hit.object_type=hitObject.GetType()`.
6. `hit.hit==false` → `result.ok=true` (no error). `result.raycast=hit; return true`. **Sin `GetCrosshairObject`**.

## Paso 3 — C2 `telemetry_read` (dos modos)
**[DESIGN] `DispatchTelemetryRead`** → `MCPTelemetry t=new MCPTelemetry(); t.mode=args.mode`.

**`object_at`** (snapshot server-auth):
1. `ValidateTelemetryArgs`: `type` no vacío, `pos` (3 floats), `radius>0`.
2. **[EXACT]** `GetGame().GetObjectsAtPosition3D(pos, radius, objs, cargos)` (`game.c:929`); filtrar `o.GetType()==args.type`.
3. `0` → `t.found=false; result.ok=true`. `>1` → `result.ok=false; result.error="ambiguous_fixture"`. `1` → seguir.
4. `t.found=true`; **[EXACT]** `VectorToArray(GetPosition(),t.pos)` (`object.c:293`), `VectorToArray(GetOrientation(),t.orientation)` (`:311`), `VectorToArray(GetDirection(),t.direction)` (`:320`), `t.class_name=ClassName()` (`enscript.c:37`), `t.type=GetType()`, `t.health01=GetHealth01("","")` (`object.c:997`), `VectorToArray(GetVelocity(o),t.velocity)` (`enphysics.c:104`). Inventario superficial **[A]**: `GetInventory()` (`entityai.c:1834`)→`AttachmentCount()`/`GetCargo().GetItemCount()` (`inventory.c`/`cargo.c`), `items[]` **acotado** (cap). Si `Car.Cast(o)`: reusar campos fase 1 (`EngineIsOn`,`GetSpeedometer`,`WheelCountPresent`,`GetFluidFraction(CarFluid.FUEL)`), **sin redseñar B3**.

**`fixture_jsonl`** (test de parser — **contrato fijo, P1-4**):
- **Allowlist fail-closed (deny-by-default; harden-prefix, R21-F2-001 / X.5 2026-06-09)**: acepta SOLO si `path` empieza por el prefijo exacto `$mission:dayz_mcp/` Y el basename (resto tras el prefijo) no es vacío ni contiene `/`, `\` o `..`; cualquier otro caso → `bad_args`. Acepta los fixtures del harness (`telemetry_fixture`/`missing_fixture`/`telemetry_bad`.jsonl) y rechaza traversal y rutas fuera del prefijo. (NO single-exact: una sola ruta exacta rompería los negativos `fixture_not_found`/`parse_error`, que dependen de rutas distintas bajo el prefijo — ripple R7.)
- **Productor**: `run-fase2.ps1` escribe el fixture al mission workspace **antes** de lanzar (NO el bridge → anti-tautología LL-115).
- **Contenido EXACTO** (2 líneas):
  `{"fixture_id":"fx1","value":42.0,"seq":0}`
  `{"fixture_id":"fx2","value":7.5,"seq":1}`
- **DTO**: `MCPTelemetryFixtureLine {string fixture_id; float value; int seq;}`.
- **Lectura**: **[A, re-verificar impl]** `OpenFile(path, FileMode.READ)` (`ensystem.c:417`); si `==0` → `t.found=false; result.ok=false; result.error="fixture_not_found"`. Bucle `FGets` (`ensystem.c:501`) hasta `max_lines`; por línea `JsonSerializer.ReadFromString`/`JsonFileLoader.LoadData` (`jsonfileloader.c:69`) → `MCPTelemetryFixtureLine`. **NO `JsonFileLoader.LoadFile`** (lee `READ_FILE_LENGTH=100000000`, `jsonfileloader.c:3/19`). `CloseFile`.
- **Expected del verdict**: `t.found=true`, `t.line_count_read==2`, `t.last_valid.fixture_id=="fx2"`, `value==7.5`, `seq==1`, `parse_error==""`. Línea malformada → `result.ok=false; result.error="parse_error"`.

---

## Paso 4 — Harness Python + verdict (P1-3)
- **[EXACT]** `mcp_server.py:14`: `WHITELISTED_COMMANDS += {"scene_raycast","telemetry_read"}`.
- **[EXACT]** `mcp_client.py:629`: `--mode` choices += `"phase2"`; `:638` output name → `fase2-verdict.json` cuando `mode=="phase2"`.
- **Suite `phase2`** en `mcp_client.py`: ejecuta la matriz C1/C2 de abajo y emite verdict con gate PASS/FAIL.
- **`run-fase2.ps1`** (clon de `run-fase1.ps1`): además de spawnear vehículo + cliente, **escribe `telemetry_fixture.jsonl` al mission workspace** (contenido EXACTO de arriba) antes de lanzar. Spawnea/asegura un target estático conocido (muro/edificio) y registra su pos.

## Mapeo cliente/servidor (obligatorio)
- Todo server-side en `MissionServer.Dispatch`. Raycast/telemetry leen estado autoritativo.
- **Raycast exige cliente conectado** (LL-093) — call server-side; el harness conecta cliente real.
- **A verificar in-game**: que `GetHealth01`/inventario/`GetVelocity` devuelven valor en MissionServer (salud/pos son `Object` base → server-auth; inventario [A]).

## Spike + matriz de validación (un rebuild, R5)
**C1** (ambas APIs × dinámico + estático):
- POS dinámico: raycast a vehículo a distancia conocida → `hit=true`, `object_type` esperado, `distance` con tolerancia, `normal≠0`. `method=rvproxy` y `method=bullet`; **comparar fidelidad de la normal** (elige primaria).
- POS estático: raycast a muro/edificio a distancia conocida → confirma que el server golpea **geo estática**.
- NEG: rayo a vacío → `ok=true`, `hit=false`, sin exception ni job.
- PROXY: si `hier_level>0`, el verdict compara `parent_type`, no solo `object_type`.
- ORDEN: target con varios componentes → confirmar que el nearest elegido (DistanceSq) es el correcto, no `res[0]`.
**C2**:
- `object_at` POS: snapshot de la entidad fixture → tipo/pos/health01/inventario esperados.
- `fixture_jsonl` POS: parse del JSONL conocido → `last_valid.fixture_id=="fx2"`, `line_count_read==2`.
- NEG: path no-allowlist → `ok=false bad_args`; archivo ausente → `ok=false fixture_not_found`; JSON inválido → `ok=false parse_error`; entidad no encontrada → `ok=true found=false`; ambigua → `ok=false ambiguous_fixture`.
**Backpressure**: batch C1/C2 respeta cap 4/tick y `bridge_queue_full` sin jobs nuevos.
**Anti-tautología (LL-115)**: distancia/pos/fixture esperados independientes de la lectura del bridge; resultado exacto `0.000` sospechoso (R22 global).

## Gates de validación (orden)
1. **Source/compile-clean** (Enforce 1.29: sin ternario `?:`; `Print(string.Format(...))`; sin `new` en ticks; **arrays por componentes, no `=vector`**).
2. **Harness Python listo** (whitelist + `--mode phase2` + suite + `run-fase2.ps1` con fixture).
3. **In-game spike** (cliente conectado): C1 ambas APIs × din+estático + C2 ambos modos, agrupado.
4. **Elegir primaria de C1** por el dato; registrar en `decision-log.md` + HANDOFF; (opcional) podar el path no elegido.
5. **Cierre**: marcar C1/C2 ✓ en product-spec; anotar drop de `GetCrosshairObject` (Changelog product-spec + architecture §6).

## Fuera de alcance (R25)
- `get_property`/`set_property` genérico (Enforce sin reflexión por nombre).
- Captura visual / cámara (fase 3).
- Reporte **multi-hit completo** (sí se itera para elegir el nearest, pero solo se devuelve 1 hit).
- Fix del NIT del reloj float `m_ElapsedS` (deuda daemon, no POC).
- Conducción B3 / client-peer (fase diferida).

## Riesgos (con mitigación)
- Geo estática no carga server-side aun con cliente → C1 limitado a dinámicos: lo decide el spike (target estático). Plan B: documentar límite y acotar C1 a entidades.
- `RaycastRVResult.dir` no da normal fiable → primaria `RayCastBullet` (normal nativa). Cubierto por "spike mide ambas".
- `SurfaceInfo`/inventario/`OpenFile`/layer mask [A] sin re-leer host-direct → Codex re-verifica firmas en impl (R2).
- Vitals/inventario podrían ser client-cached → gate in-game los valida.

---
**Siguiente paso**: este plan v2 ya integra R22. Si Codex valida el v2 (o el usuario confirma), Codex implementa por pasos (0→4) usando este plan como spec.
