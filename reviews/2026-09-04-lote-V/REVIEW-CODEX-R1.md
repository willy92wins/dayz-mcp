## VEREDICTO

BLOQUEANTES=2.

## BLOQUEANTES

### B-01 — V2 afirma `no_player_connected` sin haber recibido una lista de jugadores

**Producto roto:** V2 exige añadir `reason: "no_player_connected"` sólo cuando la sonda `query_all_players` es correcta **y su lista está vacía**; en cualquier otro caso no debe existir `reason`.

**Ubicación:** `tools/dayz_mcp/server.py:1897-1899` convierte un campo `players` ausente, `null` o falsy en `[]`; después `tools/dayz_mcp/server.py:1919-1920` interpreta ese valor sintetizado como evidencia de lista vacía. Es una afirmación positiva falsa ante una respuesta correcta pero malformada/incompleta. El propio helper `_player_count` sí distingue una lista real de un valor ausente o no-lista en `tools/dayz_mcp/server.py:1800-1804`.

**Repro literal [EXACT]:**

```powershell
Set-Location 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-V\ws\tools'
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import asyncio, json
from unittest.mock import patch
from dayz_mcp import server

def content_json(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)

async def main():
    app, runtime = server.build_app(server.ServerConfig(key="k", port=0, log_sink=lambda _m: None))
    async def fake(cmd, args, peer, timeout_s):
        if cmd == "entities_query":
            return {"ok": 1, "entities": []}
        if cmd == "query_all_players":
            return {"ok": 1}
        raise AssertionError(cmd)
    with patch.object(runtime, "call_bridge", fake):
        result = content_json(await app.call_tool("entities_query", {"pos":[0.0,0.0,0.0], "radius":1.0}))
    print(json.dumps(result, sort_keys=True))
    assert "reason" not in result, "probe had no players list, so absence was not positively established"

asyncio.run(main())
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
exit $LASTEXITCODE
```

**Salida hoy (exit 1):**

```text
{"entities": [], "nearest_player_m": null, "ok": 1, "reason": "no_player_connected", "reliability": "remote_unverified"}
AssertionError: probe had no players list, so absence was not positively established
```

**Corrección sugerida:** conservar por separado el valor crudo de `players` y añadir `reason` únicamente si `isinstance(players_raw, list)` y `len(players_raw) == 0`; alternativamente, rechazar explícitamente la respuesta correcta malformada. No convertir ausencia o tipo inválido en una lista vacía.

**Riesgo real:** el Enforce actual sí rellena `players` en `addon/scripts/5_Mission/MCPBridge.c:455-458`, por lo que la probabilidad con ese productor es baja. Eso no satisface el contrato observable de la función pública: la rama afirma un hecho que la sonda no demostró y falla mediante `app.call_tool`, exactamente en la superficie V2.

### B-02 — V4 extiende el deadline al dormir siempre el intervalo completo

**Producto roto:** V4 exige reintentar `server_poll_stale` hasta un deadline único, sin extenderlo, y devolver un `elapsed` aproximadamente igual a `timeout_s`, nunca superior por un intervalo completo.

**Ubicación:** `tools/dayz_mcp/server.py:2363-2364` calcula una sola vez el deadline, pero `tools/dayz_mcp/server.py:2487-2488` ejecuta siempre `asyncio.sleep(poll_interval_s)` sin acotarlo al tiempo restante. La comprobación siguiente del deadline ocurre después del exceso. Con `timeout_s=0.1` y `poll_interval_s=0.5`, la llamada tarda aproximadamente 0.508 segundos: más de cinco veces el plazo solicitado.

**Repro literal [EXACT]:**

```powershell
Set-Location 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-V\ws\tools'
$env:PYTHONPATH='.'
$env:PYTHONDONTWRITEBYTECODE='1'
@'
import asyncio, json, time
from unittest.mock import patch
from dayz_mcp import server

def content_json(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)

async def main():
    app, runtime = server.build_app(server.ServerConfig(key="k", port=0, log_sink=lambda _m: None))
    calls = 0
    async def stale(cmd, args, peer, timeout_s):
        nonlocal calls
        calls += 1
        raise server.ToolError("game_not_ready:reason=server_poll_stale")
    with patch.object(runtime, "call_bridge", stale):
        t0 = time.monotonic()
        result = content_json(await app.call_tool("wait_for", {"condition":"players_at_least", "value":1, "timeout_s":0.1, "poll_interval_s":0.5}))
        wall = time.monotonic() - t0
    print(json.dumps(result, sort_keys=True))
    print(f"wall_s={wall:.3f} calls={calls} deadline_s=0.100")
    assert wall <= 0.15, f"deadline exceeded: {wall:.3f}s"

asyncio.run(main())
'@ | & 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -
exit $LASTEXITCODE
```

**Salida hoy (exit 1):**

```text
{"condition": "players_at_least", "elapsed_s": 0.5080714999930933, "last_error": "game_not_ready:reason=server_poll_stale", "not_ready_probes": 1, "observed": null, "ok": true, "probes": 1, "satisfied": false, "timed_out": true, "tool": "wait_for"}
wall_s=0.508 calls=1 deadline_s=0.100
AssertionError: deadline exceeded: 0.508s
```

**Corrección sugerida:** antes de dormir, recalcular el tiempo restante y dormir `min(poll_interval_s, remaining)` sólo si es positivo. También conviene acotar el timeout de cada nueva sonda al tiempo restante real; el deadline debe gobernar tanto sondeo como espera.

**Criterio:** que un servidor permanezca `server_poll_stale` durante todo el timeout no es por sí mismo un defecto: esperar a través del arranque es el contrato pedido. El defecto es que la nueva rama reintentable mantiene al llamador esperando más allá del plazo único publicado.

## BACKLOG

- **Cobertura del oráculo insuficiente para B-01.** `gate/oracle.py` cubre vacío, jugador lejano, sonda `ok: 0` y consulta `ok: 0`, pero no una sonda `ok: 1` sin una lista válida. Es un defecto del aparato y, por mandato del alcance, no bloquea por sí mismo.

- **Gate temporal demasiado laxo para B-02.** El oráculo acepta `elapsed < 3.0` para un timeout de 1.2 segundos y no prueba `poll_interval_s > timeout_s`. Por eso queda verde aunque el plazo pueda excederse hasta casi un intervalo completo. También es aparato del orquestador y queda aquí, no como bloqueante adicional.

- **Comentario interno contrario al contrato abierto de V1.** `tools/dayz_mcp/server.py:113` todavía dice `Closed ready.reason set`, aunque la descripción pública declara correctamente que `reason` es un conjunto abierto. Es deuda documental interna/cosmética; el comportamiento público V1 sí es correcto.

- **Contador de skips no reproducido literalmente.** `RONDA2-V.md` registra `skipped=2`; mi ejecución fresca de `bash gate/suite.sh` terminó verde con `skipped=3` en `tests.test_docs_truth`. No observé fallo de V1–V5 y no investigué más el aparato, como exige el límite de alcance.

- **No encontré consumidores internos frágiles ante las claves nuevas de V4.** La búsqueda de `not_ready_probes` y `last_error` sólo encontró la implementación y sus tests; tampoco encontré playbooks que llamen a `wait_for` ni comparaciones de su respuesta por igualdad completa de diccionario. Los consumidores externos no forman parte del repo disponible.

- **V1 verificada.** `_bridge_status_public_description()` lee `READY_REASONS` y `_FENCE_BLOCK_READY.values()` al registrar la tool en una app nueva (`tools/dayz_mcp/server.py:2772-2781`, `tools/dayz_mcp/server.py:4141`). El mutante del oráculo apareció en una app nueva. El recorrido de retornos de `compute_bridge_ready` (`tools/dayz_mcp/server.py:379-422`) no reveló un valor fuera de esa unión: 17 valores publicados y cero faltantes. No hay copia pública congelada ni test que limite la lista a los seis valores antiguos.

- **V2, casos nominales verificados por `app.call_tool`.** Sonda correcta vacía añade `reason`; sonda correcta con jugador lejano no lo añade; sonda `ok: 0` no lo añade; una consulta `ok: 0` queda intacta. El único fallo bloqueante es el caso de éxito sin lista descrito en B-01.

- **V2 no colisiona con un campo Enforce existente.** `MCPResult` define `count_total` y `entities` para esta respuesta, no `reason`, en `addon/scripts/5_Mission/MCPMessages.c:423-491`; `DispatchEntitiesQuery` sólo rellena esos campos y `ok` en `addon/scripts/5_Mission/MCPBridge.c:1382-1434`.

- **V3 verificada.** `tools/dayz_mcp/server.py:2334-2341` rechaza valores mayores de 600 antes de reloj, lock o sonda; 600.0 se acepta; `_finite_float` rechaza NaN e infinitos en `tools/dayz_mcp/server.py:1690-1702`; el wrapper público reenvía sin otro camino alternativo en `tools/dayz_mcp/server.py:4510-4535`. La prueba mediante cliente MCP real devolvió `bad_args: timeout_s must be <= 600` y contó cero llamadas al bridge. El párrafo de `tools/README-mcp.md:137` sólo cambia `capped` por `rejected` y concuerda con el código.

- **V4 restante verificada.** La comparación del error reintentable es exacta en `tools/dayz_mcp/server.py:2431-2436`. `client_not_polling`, `no_run`, `version_blocked`, `daemon_unavailable` y `server_poll_stale` con sufijo abortaron en la primera sonda. `probes`, `not_ready_probes` y `last_error` se propagan correctamente. El sleep está fuera de `runtime.tool_lock`, y el contendiente del oráculo adquirió el lock. El e2e con `require_version=True` y servidor nunca poll-eado también pasó. Nada de esto subsana el exceso de deadline de B-02.

- **V5 verificada estáticamente.** `addon/scripts/5_Mission/MCPClientBridge.c:1881` empareja `action` contra `candidate.Type().ToString()` y `addon/scripts/5_Mission/MCPClientBridge.c:1936` devuelve `classname` desde `targetObj.GetType()`. La tolerancia adicional del filtro de entrada a `ClassName()` en `addon/scripts/5_Mission/MCPClientBridge.c:2007-2013` no contradice la descripción pública: ésta identifica el valor canónico, no promete rechazar alias tolerados.

- **Transversal sin inversión adicional observada.** El oráculo terminó `PASS=27 FAIL=0 UNMET=0 de 27` / `ORACULO-VERDE`. `bash gate/suite.sh` terminó `SUITE-ACOTADA OK`. Los dos grupos pedidos de unittest ejecutaron 41 y 82 tests, respectivamente, ambos `OK`. Las dos inversiones observables que esos gates no detectaron son B-01 y B-02.

- **Mediciones adicionales realizadas frente a las del orquestador:** cliente MCP en memoria real para descripciones y rechazo temprano; sonda exitosa malformada para comprobar evidencia positiva de V2; intervalo de sondeo mayor que el timeout para discriminar deadline real en V4; censo de consumidores internos de las claves nuevas; recorrido estático de todos los retornos de `compute_bridge_ready`; y lectura del emparejamiento Enforce de V5.

## LO QUE NO PUDE VERIFICAR

- No ejecuté DayZ, daemon ni red, conforme al entorno entregado. V5 quedó verificada contra el código Enforce, no mediante una acción real dentro del juego.

- No ejecuté la suite completa del repositorio: ejecuté el oráculo, `gate/suite.sh` y los dos grupos pedidos, 123 tests unittest en total. No atribuyo a V1–V5 propiedades fuera de esa superficie.

- No pude censar consumidores externos o de terceros que comparen el diccionario completo de `wait_for`; sólo el repo y los playbooks entregados. Dentro de ellos no encontré ninguno.

- No validé comportamiento de Python distinto del intérprete obligatorio. Todos los comandos funcionales usaron exactamente `tools/.venv-mcp/Scripts/python.exe` del árbol vivo.

- No actualicé la memoria Obsidian del proyecto: está fuera de las raíces con permiso de escritura de esta sesión. Este dictamen es el único artefacto escrito.

## PREMISAS DEL ORQUESTADOR QUE CREO FALSAS

### P-01 — «V2 sólo afirma `reason` con evidencia positiva» no se sostiene para toda respuesta aceptada

La implementación no exige que exista una lista: `players_result.get("players") or []` fabrica una lista vacía cuando el campo falta. El repro B-01 refuta la premisa mediante la tool pública y sale 1 hoy. Los cuatro casos nominales del oráculo sí se sostienen; la falsedad está en el cuantificador «sólo»/«cualquier otro caso».

### P-02 — «V4 usa el mismo deadline sin extenderlo» es falsa como propiedad observable

El timestamp `deadline` no cambia, pero la llamada sí continúa después de él porque el sleep no se acota. El repro B-02 pide 0.100 segundos, observa 0.508 y sale 1. Mantener una variable constante no basta para cumplir el deadline que esa variable representa.

### P-03 — La premisa de entorno «HEAD 00d4303 y lote integrado sin commit» ya no describe el árbol vivo

**Comando de refutación [EXACT]:**

```powershell
git -c safe.directory="C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev" -C "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev" log --oneline -2
```

**Salida observada:**

```text
cd53f7c server: five ergonomics fixes to public MCP tool contracts
00d4303 tools: five fixes outside server.py — docs, validation, oracle, live-run gate, hygiene
```

El lote está ahora en `cd53f7c`, no sólo sin commitear sobre `00d4303`. Esto puede deberse a trabajo concurrente posterior al briefing y no altera el dictamen: los cinco ficheros de producto del workspace coinciden con ese commit (README coincidente tras normalizar CRLF/LF), y `DIFF-V.patch` sigue describiendo el delta revisado. No es un bloqueante de V1–V5.

Las demás premisas del §2 se sostuvieron con la evidencia disponible: autoridad dinámica V1; ausencia de consumidores internos por igualdad completa; rechazo temprano V3; igualdad exacta del motivo V4; sleep fuera del lock; y semántica Enforce descrita por V5.
