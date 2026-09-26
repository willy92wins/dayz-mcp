# BRIEF unico - council COBERTURA - renombrar `type` a `classname` en las tools del MCP

## LA PREGUNTA

Se propone unificar el kwarg de nombre-de-clase en las tools del servidor MCP de DayZ:
renombrar `type` a `classname` en las tools que hoy usan `type`.

Tu encargo NO es decidir si el rename es buena idea. Es responder: **QUE SE ROMPE**, de la
forma mas exhaustiva y concreta que puedas, y con evidencia.

## HECHOS VERIFICADOS (censo por AST del orquestador, re-derivado, no copiado)

Fichero: `DayZ_MCP_dev\tools\dayz_mcp\server.py` (4380 lineas).
Censo por AST sobre funciones `async def` decoradas con `@app.tool`:

`type` = 6 tools:
  server.py:2976  world_spawn
  server.py:3115  telemetry_read
  server.py:3160  vehicle_prepare_fixture
  server.py:3318  object_anim
  server.py:3348  infected_drive
  server.py:3429  object_inspect

`classname` = 2 tools:
  server.py:3398  inventory_give
  server.py:4106  action_use

Universo: las 8 tools cuya firma declara uno de esos dos kwargs. Metodo cruzado con regex
sobre el mismo fichero: mismo resultado. El mismo censo da 6 y 2 en el segundo arbol
(`C:\Users\guill\Repos\dayz-mcp\tools\dayz_mcp\server.py`, 4391 lineas).

AVISO SOBRE CIFRAS DE TERCEROS: circulaban un "9 tools" y un "7 tools" para esa misma
cuenta. Los dos son falsos. Si tu analisis necesita una cifra, DERIVALA del arbol y di de
donde; no reutilices ninguna cifra de este brief sin comprobarla, incluida la de arriba.

## TRES ARBOLES, NO UNO. Esto importa para tu respuesta.

  A) `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`  - trabajo, SUCIO (T13-T15)
  B) `C:\Users\guill\Repos\dayz-mcp`                                  - trabajo, SUCIO (T1-T5)
  C) commit publicado `9a58256`                                        - sin `key_press` ni `player_respawn`

## INTERPRETACIONES DEL ORQUESTADOR (NO son hechos; puedes refutarlas)

1. Creo que el rename rompe a todo llamador existente y que por eso es un cambio
   incompatible de API, no un arreglo de documentacion.
2. Creo que los llamadores incluyen playbooks, scripts, tests, documentacion y el uso ya
   aprendido de agentes vivos que hoy llaman `world_spawn(type=...)`.
3. Creo que un alias compatible (aceptar los dos kwargs durante una transicion) evitaria
   las roturas a cambio de mas trabajo.

## CRITICA OBLIGATORIA DEL ENCUADRE - RESPONDE ESTO PRIMERO

Antes de valorar nada: **que puede estar mal en el encuadre del problema o en las opciones
de arriba?** Puedes aceptar un hecho y refutar la inferencia. Si crees que la pregunta esta
mal planteada, que el rename no es el problema real, o que hay una opcion que nadie ha
puesto sobre la mesa, dilo ahi. Esa seccion vale mas que el resto.

## FUENTES OBLIGATORIAS - abrelas, no las supongas

  - `DayZ_MCP_dev\tools\dayz_mcp\server.py`            (definicion de las tools)
  - `DayZ_MCP_dev\tools\dayz_mcp\loopback.py`          (ingress / validacion de args)
  - `DayZ_MCP_dev\addon\scripts\5_Mission\MCPMessages.c`  (el otro lado del puente)
  - `DayZ_MCP_dev\tools\tests\`                        (tests que llaman a esas tools)
  - `DayZ_MCP_dev\product-spec.md` y `dayz-mcp-architecture.md`
  - busca playbooks, docs y ejemplos en los dos arboles de trabajo

Barre TODO el arbol buscando llamadores, no solo `server.py`. Incluye ficheros de datos,
JSON, markdown y cualquier cosa que mencione esos kwargs.

## CAVEATS DE ENTORNO

  - El drive `P:` puede NO existir para tu proceso. Si un documento cita `P:\X`, la ruta
    real es `C:\Users\guill\OneDrive\Documentos\DayZ Projects\X`.
  - Los ficheros estan en OneDrive. LEE, no escribas nada fuera de tu propio informe.
  - NO modifiques codigo. NO hagas commit, merge ni push. Esto es solo analisis.

## CONTRATO DE SALIDA - cabeceras literales, el arnes las busca

## ENCUADRE
Tu critica del planteamiento. Que esta mal, que falta, que opcion no se ha considerado.

## ROTURAS
Una por linea. Cada una con `path:line` y que exactamente deja de funcionar. Ordenadas de
mas grave a menos. Si algo se rompe en silencio (sin error visible), marcalo SILENCIOSA:
eso es peor que un fallo ruidoso.

## ALCANCE
Cuantos llamadores hay y donde. Declara tu universo: que buscaste, con que patron, en que
rutas. Un numero sin universo no vale.

## MITIGACION
Como lo harias tu. Si crees que hay un camino mejor que el rename, ponlo aqui.

## LO_NO_VERIFICADO
Todo lo que afirmas y NO has abierto para comprobar. Esta seccion NO puede quedar vacia por
cortesia: si esta vacia, dime por que puedes garantizar el 100%.

## REGLAS

  - Cada API, firma, fichero o cifra que cites lleva `path:line`. Sin `path:line` va a
    LO_NO_VERIFICADO.
  - No inventes rutas ni firmas. Si no lo has abierto, no lo afirmes.
  - Trabajas a ciegas: no tienes acceso a lo que digan otras lanes, y es a proposito.
  - Escribe el informe a stdout y muere al terminar.
