# b256 ? lectura por ID entregada, mutaci?n pendiente

**Implementado y con gate offline; sin verificar en el motor.** Entrega parcial coherente prevista por el brief: lectura de inventario sobre el objeto seleccionado por `object_id`, sin a?adir un verbo de mutaci?n incompleto.

[EXACT, llamada de la tool ya registrada; bridge nuevo pendiente de empaquetado/prueba]
```json
{"object_id":7,"want":["inventory"]}
```

`object_inspect` devuelve `inspect` como antes y a?ade `telemetry` con `mode="object_inspect"`, identidad/tipo/posici?n y snapshot de inventario del MISMO objeto. Se conservan `memory_points` (incluso la consulta antigua de un punto llamado `inventory`), bounding center y object_id. Tambi?n se admite el selector previo `type`+`pos`. El sentinel solo tiene sentido en `object_inspect.want`; NO se ha a?adido un tercer modo a `telemetry_read`.

El snapshot incluye `declared_slots`, `attachment_count`, `attachment_items`, `cargo_count`, `cargo_items`, `items_total`, `items_truncated`, adem?s de los campos gen?ricos de telemetr?a. Son los adjuntos y cargo inmediatos. Los arrays de tipos se limitan a 16 por array; los conteos son completos. No devuelve IDs de los hijos, su estado, cargo anidado ni la relaci?n exacta slot?item. En un objeto sin inventario, los campos de inventario quedan a cero/vac?os como en telemetry_read; no indica que tal objeto pueda aceptar items.

## Evidencia le?da

- Implementaci?n nueva: `addon/scripts/5_Mission/MCPBridge.c:1518`; resuelve una vez con `ResolveCommandObject` (`:1503`), llama `PopulateTelemetryObject(match, ...)` (`:1524`) y publica el mismo bloque de respuesta (`:1525`).
- Resolver por registro y desconocidos/eliminados: `MCPBridge.c:1567` (`ResolveCommandObject`); no se a?ade un segundo escaneo ni un fallback por posici?n al desconocer un ID.
- Pobladores existentes: `MCPBridge.c:2400` y `:2428`. La lectura que el ticket dec?a inexistente ya estaba aqu?. Forma serializable: `addon/scripts/5_Mission/MCPMessages.c:241`; `MCPResult.telemetry` en `:431`.
- La tool p?blica ya acepta `want` libre y hace passthrough del resultado: `tools/dayz_mcp/server.py:4220` funci?n `object_inspect`. El ingreso permite want por ID o posici?n (`tools/dayz_mcp/loopback.py:614`).
- El consumidor conserva bloques no vac?os, sin allowlist por verbo: `tools/dayz_mcp/result_prune.py:75`.

## Mini-auditor?a de APIs

| API | Definici?n le?da | Resultado |
|---|---|---|
| `EntityAI.GetInventory()` | `../scripts/3_game/entities/entityai.c:1834` | VERIFIED |
| `GetCargo()`, `GetAttachmentSlotId(int)`, `GetAttachmentSlotsCount()` | `../scripts/3_game/systems/inventory/inventory.c:138`, `:180`, `:184` | VERIFIED |
| `AttachmentCount()`, `GetAttachmentFromIndex(int)` | mismo `inventory.c:205`, `:220` | VERIFIED |
| `CargoBase.GetItemCount()`, `GetItem(int)` | `../scripts/3_game/systems/inventory/cargo.c:28`, `:32` | VERIFIED |
| `InventorySlots.GetSlotName(int)` | `../scripts/3_game/systems/inventory/inventoryslots.c:48` | VERIFIED |

Ninguna API Enforce nueva: se llama a un poblador probado por contrato de fuente. Eso NO prueba compilaci?n ni comportamiento del nuevo call-site.

## Qu? queda y por qu?

No se implementan attach ni cargo. A?adir una tool p?blica exige cambios funcionales en `server.py` (registro, wrapper, mapa), autorizados solo para las descripciones de 62e3/6157 en esta lane: **FUERA DE MI ALCANCE**. La propia entrega parcial de lectura est? expresamente permitida por b256. No he disfrazado el selector read-only de mutaci?n ni reutilizado `uid` como un ID de objeto.

[DESIGN] Para creaci?n en destino, seguir `DispatchInventoryGive` (`MCPBridge.c:1362`): resuelve PlayerBase por identidad y llama `player.GetInventory().CreateInInventory` (`:1394`), no sobre PlayerIdentity como dec?a el brief. Un verbo nuevo deber?a resolver un EntityAI del mundo por ID, validar classname/config, slot declarado y ocupaci?n, y ejecutar `GameInventory.CreateAttachmentEx(string typeName,int slotId)` (`../scripts/3_game/systems/inventory/inventory.c:215`) o `CreateEntityInCargo(string typeName)` (`:143`), comprobar el retorno y publicar el resultado y snapshot. Son APIs de CREAR un item nuevo; mover uno existente es otra operaci?n y exige contrato separado de identidad/ubicaci?n. No se ha dise?ado ni verificado esa transferencia.

La descripci?n p?blica de object_inspect a?n no anuncia el selector nuevo porque `server.py` est? limitado a los otros dos tickets; este ANSWER es el contrato para revisi?n. Compatibilidad: a?ade un bloque telemetry; los lectores que rechacen campos desconocidos pueden necesitar actualizaci?n. No hay borrado, persistencia nueva ni cambio de permisos. No desplegado, no PBO, no resellado.

## Pruebas

ROJO contra BEFORE, salida literal resumida de `red.txt`:
```text
Ran 7 tests in 0.090s
FAILED (failures=2)
exit code: 1
```
VERDE contra el archivo activo, `green.txt`:
```text
Ran 7 tests in 0.082s
OK
exit code: 0
```
Regresi?n existente, `regression-object-inspect.txt`:
```text
Ran 4 tests in 0.084s
OK
exit code: 0
```
Los tests estructurales no ejecutan Enforce. Prueban que la rama nueva est? conectada al objeto resuelto; los positivos/negativos Python ejercitan el ingreso, read-only/lease, wrapper p?blico y pruning reales con respuestas dobladas. No se presenta ese doble como simulaci?n del motor.

Lint `script_validator.py` sobre copias verificadas del fichero antes/despu?s: ambos `WARN`, exit code 2, cero errores y tres warnings `ES-GETTYPE-EXACT-MATCH`. Son los mismos puntos de comparaci?n exacta de classname (desplazados nueve l?neas a partir de la inserci?n). Aqu? GetType exacto es el contrato existente, no una comprobaci?n de herencia de acciones. No se corrigen fuera de alcance. Los logs JSON completos y comandos est?n en `lint-before.txt`/`lint-current.txt`. No es lint del addon completo ni compilaci?n de los dos lados.

Gate de motor propuesto: dos contenedores del mismo tipo muy cercanos, obtener IDs distintos por world_spawn, llenar uno y comparar conteos por ID, comprobar IDs desconocidos/eliminados y un objeto sin EntityAI. Prohibido ejecutarlo por este brief.
