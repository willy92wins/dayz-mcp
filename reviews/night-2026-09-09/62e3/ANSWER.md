# 62e3 ? Contrato publicado

La descripci?n de `telemetry_read` se ha reescrito en `tools/dayz_mcp/server.py:3879`.
No cambia la firma ni la ejecuci?n. Verificaci?n est?tica; no se ha invocado el MCP.

| Afirmaci?n | Fuente le?da |
|---|---|
| Dos modos, argumentos exclusivos; otros strings producen `bad_mode`; transporte servidor | `tools/dayz_mcp/server.py:3906` (funci?n `telemetry_read`, localizar por s?mbolo si se mueve) |
| Radio m?ximo 50 m, inventarios truncados a 16, JSONL m?ximo 64 y l?nea 4096 caracteres | `addon/scripts/5_Mission/MCPBridge.c:17` |
| Tipo exacto, vector sin surface snap, cero resultados es ?xito con `found=false`; m?ltiples es `ambiguous_fixture` | `addon/scripts/5_Mission/MCPBridge.c:2276` (`DispatchTelemetryObjectAt`) |
| Campos comunes y espec?ficos de coche; no introspecci?n arbitraria | `addon/scripts/5_Mission/MCPBridge.c:2400` (`PopulateTelemetryObject`) |
| Slots del padre, conteos, arrays de nombres; adjuntos/cargo inmediatos, no recursi?n ni asociaci?n slot?item | `addon/scripts/5_Mission/MCPBridge.c:2428` (`PopulateTelemetryInventory`) |
| Forma tipada completa del bloque `telemetry` | `addon/scripts/5_Mission/MCPMessages.c:241`; envoltorio en `:423` |
| Prefijo `$mission:dayz_mcp/`, hoja directa, prohibici?n de `..`, default/clamp de max_lines | `addon/scripts/5_Mission/MCPBridge.c:1852` (`ValidateTelemetryArgs`) |
| Lectura desde inicio, se conserva el ?ltimo registro v?lido del prefijo, esquema fijo y errores | `addon/scripts/5_Mission/MCPBridge.c:2322` (`DispatchTelemetryFixtureJsonl`) |
| `fixture_id`, `value`, `seq` y valores centinela | `addon/scripts/5_Mission/MCPMessages.c:222` |

`items` combina adjuntos primero y cargo despu?s, hasta 16; `attachment_items` y `cargo_items` tienen cada uno su propio l?mite 16. `items_total` suma los conteos completos. `declared_slots` no est? truncado por ese l?mite. El DTO compartido puede serializar defaults de campos no aplicables: un `engine_on_server=false` en un no-coche no es una medici?n de energ?a de un sorter. Un JSONL es un fixture producido expl?citamente, no un volcado gen?rico de miembros de una entidad.

## Enum propuesto, sin aplicar

[DESIGN] Cambiar `mode: str` por `Literal["object_at", "fixture_jsonl"]` para publicar el enum que refleja el runtime. Las dos cadenas v?lidas conservan su representaci?n JSON y son compatibles. Rompe clientes que env?an cadenas desconocidas esperando `bad_mode`: recibir?an un rechazo de validaci?n previo; tambi?n cambia el fingerprint del esquema y puede afectar snapshots/clientes generados. Que hoy sea `str` es verificable en la firma; el motivo hist?rico de esa elecci?n no est? documentado en el c?digo le?do. No lo atribuyo a extensibilidad deliberada.

## Correcci?n a la premisa

Ya existe lectura de adjuntos y cargo por este modo; b256 debe centrarse en mutaci?n y/o selecci?n inequ?voca por ID. No hay lectura del miembro sincronizado del sorter, ni en esta funci?n ni en su DTO. Una condici?n de espera no puede recuperar un dato que nunca se publica.
