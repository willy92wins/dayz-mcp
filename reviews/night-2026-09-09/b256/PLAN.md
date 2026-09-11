# b256 ? alcance cerrado antes de implementar

La lectura ya existe en `telemetry_read(object_at)`: slots declarados, adjuntos/cargo inmediatos, tipos y conteos (62e3). Se entrega una ampliaci?n coherente: `object_inspect(object_id=...,want=["inventory"])` devuelve adem?s `telemetry`, calculada sobre EL MISMO objeto resuelto por ID. Tambi?n funciona por tipo+posici?n con las reglas de resoluci?n existentes. No se a?ade un verbo de mutaci?n a medias.

La palabra `inventory` conserva su consulta como memory point; a?adir el snapshot no elimina ni cambia ese resultado anterior. Se reutiliza `PopulateTelemetryObject`, sin nuevo DTO ni campo. No se exige que el objeto sea EntityAI: como telemetry_read, un objeto sin inventario deja conteos cero/default, y expone su tipo para interpretarlo.

| Dato | Cliente | Servidor | Mecanismo |
|---|---|---|---|
| Objetivo por ID del registro | No usado | ResolveCommandObject | object_inspect existente |
| Slots/adjuntos/cargo | No usado | PopulateTelemetryInventory | Bloque telemetry existente |

Mutaci?n attach/cargo y descripci?n p?blica nueva en server.py: FUERA DE MI ALCANCE por los l?mites del fichero server.py; para un verbo nuevo habr?a que revisar tambi?n los campos de MCPMessages.c y el mapa p?blico _BRIDGE_COMMAND_TOOLS en server.py. Se documentar? propuesta sin tocar esas fuentes.

Gate estructural con ROJO contra BEFORE: selector llama al poblador sobre `match`, despu?s de resolver y antes de publicar ?xito; no cambia la lectura previa de memory points; el consumidor Python conserva el bloque no vac?o. Pruebas de ingreso por ID/ruta y error, sin daemon. Lint de Enforce offline. No se afirma comportamiento del motor.
