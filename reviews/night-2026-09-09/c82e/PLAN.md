# c82e ? decisi?n antes de implementar

Entrega parcial por frontera expl?cita: `server.py` solo permite descripciones de tickets 1 y 2. La firma p?blica y `execute_wait_for` son FUERA DE MI ALCANCE en el ?rbol activo. Construir? `server.py.CANDIDATE` y su diff, sin aplicarlo, con pruebas unittest sobre ese candidato y control contra `server.py.BEFORE`.

[DESIGN] `wait_for(condition="entity_state", entity={type,pos,radius,field,equals})` sondea el verbo existente `telemetry_read(mode="object_at")`. Campos cerrados: found (bool), health01 (0..1), attachment_count, cargo_count, items_total (enteros >=0). Comparaci?n de igualdad; ausencia solo puede satisfacer found=false. Error de bridge, esquema o campo ausente aborta; no se transforma en false. Presupuesto y locks siguen el bucle actual. No ampliar Enforce ni protocolo.

## Mapa de datos

| Dato | Cliente | Servidor | Puente |
|---|---|---|---|
| Existencia, salud, inventario inmediato | No consultado | Observado | telemetry_read ya existente |
| Miembro sincronizado del sorter | No le?do | No instrumentado | Ninguno: NO se resuelve en este candidato |

Ni `entities_query` (`MCPBridge.c:1415`) ni `object_inspect` (`:1493`) publican ese miembro: enumeraci?n geom?trica/cargo-capacity y memory points, respectivamente. `telemetry_read` (`:2400`) aporta algunos estados reutilizables. A?adir lectura espec?fica del sorter exige primero localizar su clase y contrato; no asumir energ?a de coche = alimentaci?n del sorter.

Gate offline: positivos igualdad tras varios sondeos y acceso p?blico; negativos ausencia, campo desconocido, respuesta inv?lida, ambig?edad, errores de ownership; timeout y lock liberado durante sleep. ROJO contra BEFORE y VERDE candidato. No es un gate del motor ni publicaci?n de la nueva condici?n.
