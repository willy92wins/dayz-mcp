# Revisión cruzada — M06-UI-INGRESS

## Premisa

El contrato de ingress citado por el encargo sí coincide con la autoridad abierta: los cuatro verbos públicos son `ui_tree`, `ui_set_text`, `ui_click` y `ui_focus`, y `root` es opcional (`plans/inbox-20260830/00-execution-dag.md:99-105`); `ui_click` añade `mode="direct"|"complete"` y `bubble` (`plans/inbox-20260830/00-execution-dag.md:125-128`). El diff de producto se limita a las cuatro entradas de `_COMMAND_ARG_SCHEMAS` (`tools/dayz_mcp/loopback.py:636-669,700-709`).

La inferencia «el ingress era el eslabón que cortaba» no acredita todavía alcanzabilidad de punta a punta. En el árbol candidato, las firmas públicas de `server.py` aún no exponen `root` en ninguno de los cuatro verbos ni `mode`/`bubble` en `ui_click` (`tools/dayz_mcp/server.py:4109-4122,4128-4139,4145-4156,4205-4215`). Esto es coherente con el DAG: M22 es el dueño exclusivo de `server.py` y sucede después de M06 (`plans/inbox-20260830/00-execution-dag.md:57,71`). Por tanto, no es un defecto bloqueante de este producto acotado, pero M06 por sí solo no vuelve alcanzables esos parámetros desde las tools públicas.

También es demasiado fuerte decir que Enforce ya «consume» toda la capacidad por el mero hecho de declarar los campos. `mode="complete"` se rechaza deliberadamente con `mode_not_implemented` (`addon/scripts/5_Mission/MCPClientBridge.c:1445-1454`), conforme al gate de viability del contrato (`plans/inbox-20260830/00-execution-dag.md:125-128`), y `bubble` no entra aún en una rama ejecutable. Esto no contradice el contrato modular de ingress, pero impide usar esta ronda como evidencia end-to-end.

Construí una matriz independiente de 162 payloads JSON para los cuatro verbos. Los casos válidos e inválidos ordinarios dieron 0 discrepancias frente al contrato; aparecieron 2 excepciones para valores no hashables de `mode`, descritas abajo. Las claves desconocidas siguieron devolviendo `(False, "bad_args")`. El matcher común mantiene el rechazo por `keys - required - optional` (`tools/dayz_mcp/loopback.py:738-749`), y el diff no modifica entradas ajenas a los cuatro verbos UI.

## Hallazgos BLOQUEANTES (producto, con repro ejecutable)

### B-01 — `ui_click.mode` con array u objeto lanza `TypeError` en vez de `bad_args`

Severidad concreta: **exception de petición**; no hay evidencia de caída del proceso.

La nueva entrada de `ui_click` conecta `mode` con `_one_of("direct", "complete")` (`tools/dayz_mcp/loopback.py:657-665`). `_one_of` evalúa directamente `value in accepted_values`, donde `accepted_values` es un `frozenset` (`tools/dayz_mcp/loopback.py:363-368`). Tras decodificar JSON, un array es `list` y un objeto es `dict`; ambos son no hashables, por lo que el validador lanza `TypeError` antes de devolver `(False, "bad_args")`. `validate_command_args` no captura esa excepción (`tools/dayz_mcp/loopback.py:752-767`), y la ruta HTTP tampoco la convierte en una respuesta 400 entre `do_POST`, `_handle_enqueue` y `enqueue_command` (`tools/dayz_mcp/loopback.py:2619-2628,2722-2741`). Esto viola el contrato literal «todo lo demás sigue siendo `bad_args`».

[EXACT] Repro ejecutado contra la copia entregada:

```powershell
Set-Location -LiteralPath 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-C\ws\tools'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -c "from dayz_mcp.loopback import validate_command_args; print(validate_command_args('ui_click', {'path':'A','mode':[]}))"
```

Resultado observado: exit code 1 y `TypeError: cannot use 'list' as a set element (unhashable type: 'list')`, originado en `loopback.py:367`. Sustituir `[]` por `{}` reproduce la misma familia.

Fix sugerido: hacer que `_one_of` sea total para cualquier valor JSON y devuelva `False` ante valores no hashables —por ejemplo, capturando `TypeError` alrededor de la pertenencia—; después fijar regresiones con `mode: []` y `mode: {}` tanto sobre `validate_command_args` como sobre `/enqueue`.

## Backlog (todo lo demás, incluido el aparato)

- **M22 — exposición pública pendiente.** Añadir `root` a las cuatro firmas públicas y `mode`/`bubble` a `ui_click`, con validación y transporte de esos campos. No bloquea M06 porque `server.py` pertenece expresamente a M22 (`plans/inbox-20260830/00-execution-dag.md:57`), pero sí bloquea cualquier afirmación posterior de alcanzabilidad pública end-to-end.
- **Cobertura de enum no hashable.** El helper `_one_of` también alimenta otros comandos (`tools/dayz_mcp/loopback.py:484,581,684,693`). La corrección debería fijar el comportamiento fail-closed común y cubrir al menos un consumidor no UI para evitar una solución local que deje la misma excepción en la tabla. Es backlog fuera del producto de esta ronda.
- No encontré otro defecto del aparato que merezca registro en esta ronda.

## Lo que no pude verificar

- No ejecuté juego, MCP ni un request HTTP real, conforme al límite del briefing. El repro ejercita directamente la función que define el producto y la propagación HTTP se verificó por lectura del camino citado.
- No corrí la suite completa ni usé los oráculos suministrados. La exploración dinámica fue la matriz independiente de 162 payloads descrita arriba.
- No pude acreditar la conducta runtime de `root`, `complete` o `bubble`; además, el propio árbol declara `complete` todavía no implementado y M22 aún debe materializar la superficie pública.

## Veredicto: FINDINGS

Una ronda, un bloqueante reproducible: un valor JSON inválido pero perfectamente decodificable escapa del contrato `bad_args` mediante `TypeError`. No hace falta otra ronda de revisión; corresponde corregir B-01 y aplicar el gate de cierre que haya fijado el orquestador.
