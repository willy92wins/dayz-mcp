# Plan del orquestador — `type` → `classname`

Escrito DESPUÉS de leer las tres lanes ciegas y DESPUÉS de re-verificar sus citas
load-bearing en disco (C15). Ronda de reconciliación pendiente: las lanes ven este plan,
no se ven entre sí todavía.

## Veredicto en una línea

**El rename es viable y barato, pero NO como se planteó.** Va como *alias de schema
público* con un helper que ya existe y ya está en producción; la premisa original —que
`type` y `classname` son dos nombres de lo mismo— es falsa y hay que resolver la semántica
antes de tocar nada de respuesta.

## Lo que aportó cada lane (cobertura, no ranking)

| Lane | Hallazgo decisivo | Estado |
|---|---|---|
| claude | La premisa es falsa: `type` = `GetType()`, `classname` = `ClassName()`, y **coexisten en el mismo struct** (`MCPMessages.c:353`, `:45`, `:109`; `MCPBridge.c:1411-1412`) | COMPLETE |
| codex | Refuta dos inferencias mías con evidencia, y **se autodeclara CONTAMINATED** | CONTAMINATED (cosechado, etiquetado) |
| grok | **`_patch_public_argument_alias` ya existe** (`server.py:1526-1544`) y ya se usa en `scene_raycast` (`:4298`) | COMPLETE, sin `## ENCUADRE` |

Solape entre lanes: prácticamente cero. Es el resultado que justifica una corrida de
cobertura: cada una trajo la pieza que a las otras les faltaba.

## Lo que YO re-verifiqué antes de escribir esto

- `MCPMessages.c:353` — el comentario que separa `GetType()` de `ClassName()`. **Cierto.**
- `MCPBridge.c:1411-1412` — la asignación que los usa distintos. **Cierto.**
- `MCPArgs` declara `string type;` (`:45`) y `string classname;` (`:109`). **Cierto:
  el nombre destino ya está ocupado.**
- Los tres playbooks (`box_is_mine`, `place_safely`, `run_really_started`) usan sólo
  `session_status`, `bridge_status`, `surface_query`, `scene_raycast`,
  `query_all_players`, `entities_query`. **Cero `type=`. Mi inferencia era falsa;
  codex tenía razón.**
- `action_use` acepta **las dos** en el filtro: `if (found.GetType() == classFilter)` …
  `else if (found.ClassName() == classFilter)`. La lane claude citó sólo el lado de la
  respuesta (`result.classname = targetObj.GetType()`). **La ambigüedad es peor de lo que
  dijo: misma clave, either-match a la entrada y GetType-only a la salida.**
- `_patch_public_argument_alias` en `server.py:1526-1544`, usada en `:4298`. **Cierto, y
  conserva la obligatoriedad remapeando la lista `required`.**

## El plan

**Paso 0 — bloqueante, y no es el rename.** Decidir si el agente necesita alguna vez
`ClassName()` o si `GetType()` es lo único útil. Mientras `classname` signifique una cosa
en `entities_query` y otra en `action_use`, cualquier rename mueve 6 tools a un nombre ya
ambiguo. Si la respuesta es «sólo `GetType()`», lo que sobra es el campo `ClassName()` y el
arreglo barato es renombrar **ése** a `script_class`, que casi nadie consume.

**Paso 1 — el arreglo urgente, independiente del rename.** El bloque `instructions=`
miente hoy sobre el placement y ha perdido el manual operativo. Ya está en marcha en el
Track A. No espera al paso 0.

**Paso 2 — el rename, si se quiere, por alias de schema.**
`_patch_public_argument_alias(app, "world_spawn", "type", "classname")` y las otras cinco.
Consecuencias, todas verificadas contra el árbol:
- El wire sigue emitiendo `"type"`. `MCPArgs`, `MCPBridge.c:553` y el PBO: **intactos**.
- `loopback.py` intacto: los 4 schemas cerrados siguen casando.
- `EXPECTED_BRIDGE_VERSION` **no se toca**; nadie tiene que redesplegar.
- Los 4 gates por HTTP crudo y `drive_ladder.py:186` siguen verdes.
- La obligatoriedad se conserva en el schema publicado; no degrada a `ToolError`.

**Paso 3 — lo que NO se hace nunca.** Cambiar la clave de wire sin subir
`MCP_BRIDGE_VERSION` y `EXPECTED_BRIDGE_VERSION` en el mismo commit; renombrar con `sed`
(hay `type` que no son classname en `MCPMessages.c:387`, `:549`, `MCPBridge.c:1110`,
`:2116`); y reutilizar el miembro `classname` que ya existe.

**Paso 4 — el hueco que vale la pena aunque no se renombre nada.** Sacar `world_spawn` y
`telemetry_read` de `_SCHEMALESS_COMMANDS` (`loopback.py:105`, `:109`). Hoy aceptan
cualquier clave extra sin rechistar, y son justo los dos que en un rename fallarían tarde
y mal. Lo señalaron dos lanes por separado.

## Disenso, verbatim y sin resumir

**claude** sobre el alias: *"el alias no es neutral. FastMCP deriva el JSON Schema de la
firma; aceptar dos nombres exige hacer ambos opcionales y validar 'exactamente uno' en el
cuerpo. Convierte un rechazo a nivel de schema (que el cliente LLM ve ANTES de llamar) en
un ToolError en ejecución. Para un modelo eso es estrictamente peor."*

**grok** sobre el alias: *"usar `_patch_public_argument_alias(app, tool, "type",
"classname")` **sin** borrar el campo del modelo. Eso es lo que ya hace `scene_raycast`
(`server.py:4298` + `1527-1544`): schema nuevo, `call_tool` viejo sigue vivo."*

**Arbitraje: gana grok, por evidencia.** La objeción de claude describe correctamente lo
que costaría implementar un alias desde cero con FastMCP, pero el repo ya no parte de cero.
El helper existe, está en producción y remapea la lista `required`, así que la premisa de
«hay que hacerlos opcionales» no se sostiene. No es que claude razonara mal: es que no
abrió `server.py:1526`, y lo declara en su propio `LO_NO_VERIFICADO` (*"No abrió el código
de FastMCP. Todo el argumento sobre el coste del alias descansa en inferencia estándar del
framework"*). Una lane que marca su propio hueco y otra que lo llena es el council
funcionando.

## Estados de lane (C12)

- `claude` — COMPLETE. Entrega persistida por el orquestador: sólo existía en contexto.
- `codex` — **CONTAMINATED por autodeclaración**: *"Un grep incidental devolvió fragmentos
  de otras lanes."* No entra en métrica de independencia. Sus hallazgos se cosechan como
  cobertura, etiquetados, porque esta corrida es COBERTURA y porque los dos que usé los
  re-verifiqué yo contra el árbol.
- `grok` — COMPLETE con contrato **incompleto**: entregó ROTURAS, ALCANCE, MITIGACION y
  LO_NO_VERIFICADO, pero **no `## ENCUADRE`**, que era la sección que el brief marcaba como
  la de más valor. Aun así trajo el hallazgo decisivo. Se cuenta, con la falta anotada.
