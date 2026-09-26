## VEREDICTO

BLOQUEANTES=0.

## BLOQUEANTES

Ninguno.

## BACKLOG

### Resultado comprobado de P1

Las tres causas llegan con el mismo código por `dayz_test_tool.execute_dayz_test_run` y por `app.call_tool("dayz_test_run", ...)`, en ambos valores de `preflight`. El prefijo `Error executing tool dayz_test_run: ` es el sobre de FastMCP; el código que contiene coincide exactamente con el de la fachada.

| Causa | Fachada (`preflight=false/true`) | Tool pública (`preflight=false/true`) |
|---|---|---|
| UUID malformado | `bad_run_id` | `bad_run_id` |
| `mode=client` sin `run_id` | `bad_dayz_test_request:client_requires_run_id` | `bad_dayz_test_request:client_requires_run_id` |
| `mode=server|all` con `run_id` | `bad_dayz_test_request:server_all_forbid_run_id` | `bad_dayz_test_request:server_all_forbid_run_id` |

El orden también es estable: un UUID malformado se rechaza en `tools/dayz_mcp/dayz_test_request.py:338-339`, antes de evaluar la combinación modo/UUID en `tools/dayz_mcp/dayz_test_request.py:359-362`; por eso `mode=server` con UUID malformado devuelve `bad_run_id`, no `server_all_forbid_run_id`. La traducción pública está en `tools/dayz_mcp/dayz_test_tool.py:178-187`, y `bad_run_id` coincide con el código preexistente de `_exact_run` en `tools/dayz_mcp/dayz_test_tool.py:206-208`.

No encontré filtración de texto arbitrario en estas ramas. La fachada sólo publica los dos tokens si `str(exc)` es igualdad exacta contra constantes del fuente (`tools/dayz_mcp/dayz_test_tool.py:179-186`); cualquier otro `TypeError` o `ValueError` cae en `bad_dayz_test_request` (`:187`). Una sonda que hizo lanzar al parser `TypeError(r"C:\Users\victim\secret.txt")` produjo:

```text
TYPEERROR_FACADE => bad_dayz_test_request
TYPEERROR_PUBLIC => Error executing tool dayz_test_run: bad_dayz_test_request
```

El `TypeError` de serialización que podría causar un llamante Python interno al pasar un objeto no serializable ocurre antes del `try`, en `json.dumps` (`tools/dayz_mcp/dayz_test_tool.py:167-178`). No es una de las tres causas de P1 ni es representable por un cliente MCP JSON, pero convendría o bien estrechar el `except` a `ValueError`, o bien envolver también la serialización si la fachada interna se considera API soportada.

La asimetría indicada existe en el mapa: su ejecución aislada da `client_requires_run_id => client_requires_run_id` y `server_all_forbid_run_id => server_all_forbid_run_id` (`tools/dayz_mcp/server.py:189-209,282-305`). No es alcanzable hoy para la misma petición pública: el primer parseo ya intercepta esas causas en `tools/dayz_mcp/dayz_test_tool.py:174-187`; después se pasan los bytes canónicos sin mutarlos a `_execute_request` (`:568-630`), y el segundo parseo usa esos mismos bytes y las mismas políticas selladas en `tools/dayz_mcp/native_launcher_transaction.py:91-114`. La tool pública, por tanto, recibe el `DayzTestToolError` prefijado de la fachada y lo reemite sin traducir en `tools/dayz_mcp/server.py:2964-3007`. La asimetría es deuda latente: si en el futuro otro estadio puede originar uno de esos dos tokens, aparecerían dos códigos públicos para la misma clase de error.

### Resultado comprobado de P2

Para terminales exitosos enumeré la tabla completa. `A` es el UUID esperado y `B` otro UUID válido. `PASS` significa que `_validate_terminal_context` acepta el terminal.

| `preflight` | `expected_run_id` | `terminal.run_id` | Árbol vivo | Entregado | Cambio |
|---|---|---|---|---|---|
| false | `None` | `None` | `terminal_invalid` | `terminal_invalid` | — |
| false | `None` | `A` | `PASS` | `PASS` | — |
| false | `None` | `B` | `PASS` | `PASS` | — |
| false | `A` | `None` | `terminal_invalid` | `terminal_invalid` | — |
| false | `A` | `A` | `PASS` | `PASS` | — |
| false | `A` | `B` | `terminal_invalid` | `terminal_invalid` | — |
| true | `None` | `None` | `PASS` | `PASS` | — |
| true | `None` | `A` | `terminal_invalid` | `terminal_invalid` | — |
| true | `None` | `B` | `terminal_invalid` | `terminal_invalid` | — |
| true | `A` | `None` | `terminal_invalid` | `terminal_invalid` | — |
| true | `A` | `A` | `terminal_invalid` | `PASS` | Cambio pedido por P2 |
| true | `A` | `B` | `terminal_invalid` | `terminal_invalid` | — |

Sólo cambia `preflight=true, expected=A, terminal=A`. No se pierde la correlación: `None` y un UUID distinto siguen dando `terminal_invalid`, tanto por llamada directa como por la tool pública. La reestructuración exacta está en `tools/dayz_mcp/dayz_test_tool.py:532-545`.

La matriz pública válida completa, con el launcher seguro simulado para emitir el terminal coherente de cada fila, produjo:

```text
mode=server run_id=None preflight=false => status=succeeded run_id=B error_code=None
mode=server run_id=None preflight=true  => status=succeeded run_id=None error_code=None
mode=all    run_id=None preflight=false => status=succeeded run_id=B error_code=None
mode=all    run_id=None preflight=true  => status=succeeded run_id=None error_code=None
mode=client run_id=A    preflight=false => status=succeeded run_id=A error_code=None
mode=client run_id=A    preflight=true  => status=succeeded run_id=A error_code=None
```

Las seis filas inválidas de la matriz (`client/None` y `server|all/A`, con ambos valores de `preflight`) siguieron rechazadas con los códigos de P1. Además, ejecuté la función real del worker con la policy y el broker fixture para `mode=client, preflight=true, run_id=A`: devolvió `exit_code=0`, conservó `run_id=A` y emitió cero peticiones al broker. Eso coincide con `tools/dayz_mcp/dayz_test_worker.py:524-525,547-552`; no depende de inferir el contrato a partir del test de fachada.

### Resultado comprobado de P3

`app.list_tools()` devolvió para la descripción registrada de `dayz_test_run`:

```text
Reattach sequence: server -> run_id -> client(run_id). client requires run_id; server|all forbid run_id. preflight does not relax that matrix.
```

Describe la secuencia, la prohibición simétrica y la invariancia de la matriz bajo `preflight`, y coincide con las comprobaciones del parser en `tools/dayz_mcp/dayz_test_request.py:338-362`. El texto registrado procede de `tools/dayz_mcp/server.py:2881-2899`.

### Transversal del diff

Comparé por SHA-256 241 archivos `.py/.json/.toml/.txt` de `tools/dayz_mcp` y `tools/tests` entre el árbol entregado y el vivo. Difieren exactamente cuatro: `dayz_test_tool.py`, `server.py`, `test_dayz_test_tool.py` y `test_mcp_tools.py`; son los cuatro de `DIFF.patch`. Los cuatro hunks de los dos archivos de producción sólo alteran P1, P2 y P3; los otros dos archivos son tests. No encontré otro camino de éxito/fallo invertido por este diff.

Comandos principales ejecutados con el intérprete exigido:

```powershell
Set-Location -LiteralPath 'C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-E\ws\tools'
$env:PYTHONPATH='.'
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_dayz_test_tool
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_mcp_tools
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_dayz_test_request tests.test_dayz_test_worker
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m unittest tests.test_client_mode
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -m py_compile dayz_mcp/dayz_test_tool.py dayz_mcp/server.py tests/test_dayz_test_tool.py tests/test_mcp_tools.py
```

Salida fresca: `45 OK`, `33 OK`, `36 OK`, `53 OK`; `py_compile` con exit 0. Total focal ejecutado: 167 tests, sin fallos.

### Deuda de pruebas y aparato — no bloqueante

1. **P1 no queda fijado por igualdad exacta.** `tools/tests/test_dayz_test_tool.py:216-264` usa `assertIn`, y `tools/tests/test_mcp_tools.py:738-799` vuelve a buscar el token como subcadena. Las pruebas podrían quedar verdes con códigos distintos entre fachada y tool. Añadir una tabla de códigos esperados completos y comparar igualdad tras retirar sólo el sobre estable de FastMCP.

2. **La prueba pública de P2 fabrica el hecho que pretende comprobar.** `tools/tests/test_mcp_tools.py:801-868` hace que el launcher simulado devuelva directamente el mismo `run_id`; no conecta la salida real del worker con el validador. El worker sí conserva hoy el UUID, como demostró la sonda independiente, pero una regresión futura en `dayz_test_worker.py:547-552` podría no ser detectada por esa prueba. Añadir un test de integración worker → terminal → fachada, o al menos un test directo del worker para `client + preflight + run_id`.

3. **P3 se comprueba sólo por palabras sueltas.** `tools/tests/test_mcp_tools.py:870-889` exige `reattach`, `preflight`, `run_id` y `client`, pero no la prohibición `server|all`, la dirección de la secuencia ni que preflight no relaje la matriz. Un texto semánticamente contrario podría pasar. Fijar las tres proposiciones, no sólo cuatro tokens.

4. **La matriz terminal no queda exhaustiva en la suite.** `tools/tests/test_dayz_test_tool.py:512-547` cubre varios controles y las dos filas nuevas, pero no la tabla 2×2×3 completa. Convertir la tabla anterior en test parametrizado reduciría el riesgo de que otro `return` temprano admita una combinación colateral.

5. **No hay regresión para `TypeError`.** La rama nueva lo declara junto a `ValueError` (`tools/dayz_mcp/dayz_test_tool.py:178`), pero los tests añadidos sólo hacen que el parser lance `ValueError`. La sonda de esta revisión acredita el comportamiento actual; falta fijarlo en la suite si se quiere mantener esa promesa.

6. **Suite auxiliar roja también en baseline.** `tests.test_request_path_authority` dio `Ran 13 tests ... FAILED (errors=10, skipped=2)` tanto en el árbol entregado como en el vivo, siempre en `_capture_root_for_test` → `_open_directory` → `invalid_dayz_test_path_authority`. `request_path_authority.py` y ese test son byte-idénticos entre ambos árboles, por lo que no es regresión de P1–P3. El informe del implementador dice `13 OK (skipped=1)`, de modo que hay deriva ambiental del aparato que conviene aislar; no cuenta como bloqueante por el alcance explícito.

## LO QUE NO PUDE VERIFICAR

- No ejecuté un cliente MCP externo por stdio. P1 se observó hasta `app.call_tool`, exactamente la superficie solicitada, pero no a través de otro proceso consumidor.
- No ejecuté el PE/launcher registrado ni DayZ. La ruta pública se probó con el launcher seguro simulado; la rama preflight del worker sí se ejecutó de verdad y conservó el UUID. Por tanto, no afirmo nada sobre empaquetado, juego, Steam, red ni PBO.
- No ejecuté la suite completa del repositorio. Ejecuté 167 tests focales y `py_compile`; la suite auxiliar de autoridad de rutas se contrastó aparte y quedó roja de forma idéntica en candidato y baseline.
- El árbol entregado no contiene un repositorio Git utilizable (`git status` devuelve `fatal: not a git repository`), así que no pude acreditar commit ni rama. Sí acredité el alcance material comparando hashes de 241 archivos contra el árbol vivo.

## PREMISAS DEL ORQUESTADOR QUE CREO FALSAS

Ninguna de las premisas del §2 quedó refutada.

- `bad_run_id` es coherente con `_exact_run` y fue idéntico por fachada y tool pública, con ambos valores de `preflight`.
- Los dos códigos prefijados atravesaron FastMCP con los dos puntos intactos.
- Las identity mappings del mapa existen y son asimétricas respecto a la fachada, pero no son alcanzables para la misma causa desde una petición pública canónica en el flujo actual. Eso es deuda latente, no una refutación de la decisión ni un defecto observable de P1 hoy.
