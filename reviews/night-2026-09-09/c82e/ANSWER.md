# c82e ? candidato implementado; integraci?n p?blica pendiente

**Implementado y con gate offline; sin verificar en el motor.** Esto se refiere al candidato `server.py.CANDIDATE`, NO al `server.py` activo. La condici?n nueva NO est? publicada en el ?rbol activo. Su aplicaci?n es **FUERA DE MI ALCANCE**: la lista exclusiva limita `server.py` a las descripciones de 62e3/6157. El brief pide modificar wait_for pero no autoriza su fichero para este ticket; eleg? conservar esa frontera, producir c?digo completo revisable y declarar la entrega parcial.

Artefactos: `server.py.BEFORE`, `server.py.CANDIDATE`, `server.py.patch`, `PLAN.md`; pruebas en `tools/tests/test_night0909_entity_wait.py`. Por defecto estos tests apuntan al CANDIDATO entregado, expresamente; no acreditan el fichero de producci?n. `NIGHT0909_SERVER_SOURCE` permite el control negativo.

## Uso del candidato

[EXACT, c?digo Python del candidato; API p?blica a?n no integrada]
```json
{"condition":"entity_state","entity":{"type":"WoodenCrate","pos":[7500.0,10.0,7500.0],"radius":3.0,"field":"cargo_count","equals":1},"timeout_s":60.0,"poll_interval_s":0.5}
```

Se sondea `telemetry_read(mode="object_at")`, servidor, tipo exacto, radio >0 y <=50 m, posici?n sin ajustar Y; las coordenadas del predicado se acotan conservadoramente a +/-1e9 y deben ser finitas. Igualdad exacta. `found` exige bool; `health01` n?mero 0..1; los tres conteos exigen entero >=0, no booleano. No admite `engine_on_server`, miembros de mod ni getters arbitrarios. Las listas truncadas no se usan como prueba de ausencia.

Sin entidad, solo `field=found,equals=false` puede satisfacer; buscar cargo=0 en una entidad ausente NO pasa. Respuesta/campo ausente o inv?lido, ambig?edad y errores del puente abortan. No hay retry silencioso de errores de ownership ni de arranque en entity_state. Duerme fuera del lock, con deadline y m?ximo de sondeo heredados. La igualdad exacta de floats es intencional: usarla solo para estados discretos/valores estables. Un timeout exitoso de transporte conserva `ok=true,satisfied=false`.

## Evidencia y l?mites

- Estado de la tool previa: `tools/dayz_mcp/server.py:85` (condiciones), `:2596` (ejecutor); snapshot exacto preservado en BEFORE.
- `entities_query` enumera tipo/posici?n/capacidad de cargo, no miembros: `addon/scripts/5_Mission/MCPBridge.c:1415` y `MCPMessages.c:356`.
- `object_inspect` original lee puntos y bounding center: `c82e/PLAN.md` cita el m?todo; ning?n lector publicado devuelve el miembro sincronizado del sorter.
- Estado reutilizado: `PopulateTelemetryObject`/`PopulateTelemetryInventory` en MCPBridge; cat?logo y l?mites probados por lectura en `../62e3/ANSWER.md`.
- `_finite_float` (`tools/dayz_mcp/server.py:1885`) convierte bool a float. Lo detect? un negativo nuevo y el candidato valida tipos del diccionario antes de llamar al helper.

**El caso real del sorter sigue sin resolverse.** Hace falta conocer su clase/miembro/getter y elegir el lado a observar. Esperar una variable en servidor no acredita que la r?plica cliente haya llegado. A?adir instrumentaci?n tipada del mod, o un proveedor expl?cito, requiere contrato y campos de respuesta; `MCPMessages.c` est? excluido. No he inventado ese API. Tampoco se acredita existencia/ausencia fuera del ?rea transmitida al jugador; `telemetry_read` no lleva la fiabilidad que a?ade entities_query.

## Pruebas ejecutadas

Comandos completos y salida literal est?n en `red.txt` y `green.txt`; resumen literal:

ROJO contra BEFORE:
```text
Ran 10 tests in 0.170s
FAILED (failures=2, errors=37)
exit code: 1
```
VERDE contra candidato:
```text
Ran 10 tests in 0.144s
OK
exit code: 0
```
El ROJO acredita la falta de firma/esquema adem?s del cuerpo; no es un fallo de importaci?n. Los 10 tests incluyen el registro FastMCP y su handler reales con Runtime inyectado, sin lifespan, socket ni transporte. El overlay de autoridad se dobla: fingerprint/autoridad del candidato NO verificados.

Revisor: revisar y aplicar solo el diff por hunks tras comparar el ?rbol actual; nunca sustituir el fichero entero por el candidato. Repetir tests apuntando al server.py finalmente integrado y comprobar autoridad/fingerprint antes de publicar. No requiere resellado nativo por PACKAGED_MODULES le?do; no se ha resellado.
