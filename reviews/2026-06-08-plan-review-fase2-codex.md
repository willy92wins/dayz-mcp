### Resumen ejecutivo
- Veredicto: approve.
- Los 6 fixes del R22 previo estan aplicados en el plan v2 como contrato implementable: no queda residual `= vector`, no queda `results[0]` como seleccion de hit, y el harness fase 2 queda dentro del Paso 4.
- La integracion no introduce FAIL/WARN que bloquee compile o falsee el verdict antes de implementar.
- Unico NIT nuevo: una frase en Paso 2 mezcla el default de `flags` con el de `type` en `RaycastRVParams`; la validacion previa del mismo paso ya lo corrige, asi que no bloquea.

### Estado de los 6 hallazgos previos
| Hallazgo | Estado (RESUELTO/PARCIAL/NO) | Seccion plan v2 | Nota |
|---|---|---|---|
| P1-1 | RESUELTO | `Revision R22 aplicada` lineas 14-16; Paso 0 lineas 30-68; C1/C2 lineas 93, 97, 107 | El plan introduce `VectorToArray(vector v, out array<float> a)` y prohibe `a = v` en `plans/2026-06-08-fase2-observacion.md:34`. Todos los campos vector->array nombrados usan el helper: raycast `pos/normal` en `:93/:97`; telemetry `pos/orientation/direction/velocity` en `:107`. Coincide con el patron real de `Insert` por componente en `MCPBridge.c:1227-1229`. DTO actual usa arrays inicializados en constructor (`MCPMessages.c:8-22`, `:41-49`), asi que el shape nuevo es tipable si se inicializan `from/to` y los arrays de sub-DTOs como pide el plan (`plans/...:36`, `:50`). |
| P1-2 | RESUELTO | `Revision R22 aplicada` linea 16; Paso 2 lineas 89-93; matriz linea 138 | El plan exige nearest por `DistanceSq` y prohibe `res[0]`: `plans/...:92`, `:138`. Verificado contra vanilla: `weapon_base.c:1815` dice que `RaycastRVProxy` no garantiza orden y `weapon_base.c:1823-1834` elige `bi` por `vector.DistanceSq`. |
| P1-3 | RESUELTO | `Revision R22 aplicada` linea 17; Paso 4 lineas 121-125; gates lineas 146-149 | Paso 4 cubre whitelist, `--mode phase2`, output `fase2-verdict.json`, suite verdict y `run-fase2.ps1`. El harness actual confirma la necesidad: `mcp_server.py:14` solo whitelist fase 0/1 y rechaza en `:116-117`; `mcp_client.py:629` solo acepta `poc|phase1` y el nombre de output esta bifurcado en `:638`; `run-fase1.ps1:556-558` ya muestra el patron real de invocar `mcp_client.py --mode phase1`. |
| P1-4 | RESUELTO | `Revision R22 aplicada` linea 18; Paso 3 lineas 109-117; Paso 4 linea 125; matriz linea 144 | El contrato deja fijo path `$mission:dayz_mcp/telemetry_fixture.jsonl`, contenido JSONL exacto de 2 lineas, DTO `MCPTelemetryFixtureLine`, expected del verdict y productor `run-fase2.ps1`, no el bridge. Esto evita la tautologia LL-115 porque el fixture esperado se escribe antes del launch desde el harness (`plans/...:111`, `:125`, `:144`). |
| P2-1 | RESUELTO | `Semantica ok/error` lineas 70-72; C1 linea 98; C2 lineas 106, 116-117; matriz lineas 136, 142 | La matriz queda clasificada: `bad_args`, `ambiguous_fixture`, `fixture_not_found`, `parse_error` son `ok=false`; entidad no encontrada es `ok=true found=false`; raycast vacio es `ok=true hit=false`. No veo caso de la matriz sin clasificar. |
| P2-2 | RESUELTO | `Revision R22 aplicada` linea 20; Paso 0 linea 42; Paso 2 linea 87 | `ignore` queda opcional: `"player"` ignora el cliente conectado si existe; default/null no depende de un player. El plan explicita que la presencia de cliente la garantiza el harness por LL-093, pero la tool no devuelve `no_players` por ello (`plans/...:87`). |

### Matriz de hallazgos nuevos (si los hay)
| ID | Seccion plan | Severidad | Resumen | Resolucion sugerida |
|---|---|---|---|---|
| RR22-001 | Paso 2, linea 90 | NIT | La frase "`p.type = intersect_type` (default `NEARESTCONTACT`)" mezcla dos campos: el source real muestra `flags = CollisionFlags.NEARESTCONTACT` en `dayzphysics.c:87` y `type = ObjIntersectView` en `dayzphysics.c:88`. El propio plan ya dice correctamente en `plans/...:86` que el default de `intersect` es `ObjIntersectView`, asi que no bloquea compile/verdict. | Cambiar la coletilla a: "`p.flags` conserva `CollisionFlags.NEARESTCONTACT`; `p.type = intersect_type` default `ObjIntersectView`". |

Sin FAIL ni WARN nuevos.

### Cite-then-verify
- `weapon_base.c:1815-1834`: existe. `weapon_base.c:1815` advierte que `RaycastRVProxy` no garantiza orden; `weapon_base.c:1823-1834` selecciona nearest por `vector.DistanceSq`.
- `MCPBridge.c:1227-1229`: existe. `BuildPlayerState` rellena `state.pos` por `Insert(pos[0])`, `Insert(pos[1])`, `Insert(pos[2])`.
- `MCPBridge.c:332-368`: existe. `Dispatch` usa `postNow=true`, `query_player_state` es sincrono, `vehicle_drive` acaba en `:354-357`, `unknown_command` empieza en `:358-362`, y `PostResult` corre en `:364-367`.
- `MCPBridge.c:370-381`: existe como molde de helper que devuelve `true` en validacion/errores inmediatos.
- `MCPBridge.c:3-5` y `:272-274`: existen. Backpressure actual: `MAX_DISPATCH_PER_TICK=4`, `MAX_PENDING=32`, `PENDING_POLL_THRESHOLD=8`, error `bridge_queue_full`.
- `mcp_server.py:14`: existe. `WHITELISTED_COMMANDS` actual = `{"query_player_state", "world_spawn", "vehicle_enter", "vehicle_drive"}`.
- `mcp_server.py:116-117`: existe. Comando fuera de whitelist devuelve HTTP 400 `{"error": "not_whitelisted"}`.
- `mcp_client.py:629`: existe. `--mode` actual solo acepta `("poc", "phase1")`.
- `mcp_client.py:638`: existe. Output actual elige `fase1-verdict.json` solo si `mode == "phase1"`, si no `poc-verdict.json`.
- `dayzphysics.c:78`, `:87-88`, `:208`, `:211`: existen. Constructor `RaycastRVParams(vBeg, vEnd, pIgnore, fRadius)`, default `flags=CollisionFlags.NEARESTCONTACT`, default `type=ObjIntersectView`, y firmas de `RaycastRVProxy` / `RayCastBullet`.
- Spot-check de citas diferidas `[A]`: `surfaceinfo.c:24-26` (`GetName`, `GetEntryName`, `GetSurfaceType`), `ensystem.c:417` (`OpenFile`), `ensystem.c:501` (`FGets`), `jsonfileloader.c:69-82` (`LoadData`) y `jsonfileloader.c:3/19` (`READ_FILE_LENGTH=100000000` + `ReadFile`) existen. No he convertido las `[A]` restantes en blockers porque el plan las difiere explicitamente a implementacion.

### Proximo paso
APPROVE: la implementacion por pasos 0->4 puede empezar.

Aplicar antes o durante el Paso 2 el NIT RR22-001 para evitar que el implementador copie una etiqueta de default incorrecta. No requiere re-R22.
