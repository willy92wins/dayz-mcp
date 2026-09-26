## A - Ficheros creados/modificados

Implementacion terminada; gate db05 y modulo principal en verde. El requisito de TODOS los modulos relacionados en verde no se cumple en esta caja: tres reproducen los mismos fallos con el codigo original. Se conserva esa evidencia, sin modificar su codigo.

Bytes medidos sobre archivos completos, no caracteres. 0 identifica un archivo nuevo de esta lane; los tamanos de STATE incluyen esta tabla.

| Ruta absoluta | Bytes antes -> despues | Cambio |
|---|---:|---|
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\dayz_test_tool.py` | 70390 -> 72917 | Publica omisiones de preflight y la causa de run_not_extensible; mantiene los tokens. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_db05_preflight_diagnostics.py` | 0 -> 9518 | 13 tests nuevos con launcher, Steam y lifecycle en memoria. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\DONE` | 0 -> 0 | Marcador de cierre vacio. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\STATE.md` | 0 -> 20180 | Informe y handoff de esta lane, escrito por tramos y consolidado al cierre. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\baseline.json` | 0 -> 590 | Censo inicial de bytes y SHA-256 de los cuatro fuentes inspeccionados. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\dayz_test_tool.original.py` | 0 -> 70390 | Copia exacta del fuente original para repetir el rojo y comparar regresiones. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\green-first.log` | 0 -> 38429 | Primera regresion: documenta las 4 comparaciones exactas de claves que obligaron a acotar el contrato. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\green.log` | 0 -> 77148 | Extracto literal de los 9 modulos verdes (323 tests) de regression.log; la cabecera declara las exclusiones. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\original-regression.log` | 0 -> 52291 | Los mismos 3 modulos rojos repetidos con dayz_test_tool original: mismas 13 identidades de fallo. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\red-first-contract.log` | 0 -> 33637 | Rojo del primer contrato, que incluia campos tambien en lanzamientos reales y la mejora Steam opcional. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\red-initial-fixture-error.log` | 0 -> 34875 | Primera calibracion: registra un token de fixture invalido que se corrigio antes de implementar. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\red.log` | 0 -> 28542 | Rojo definitivo de los 13 tests entregados contra el fuente original, antes de aplicar el arreglo final. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\regression.log` | 0 -> 128532 | Salida completa de los 12 modulos ejecutados contra el arreglo: 9 verdes y 3 rojos. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\run_original.py` | 0 -> 723 | Carga el fuente original verificado en un proceso de test aislado; no escribe el arbol productivo. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\run_tests.py` | 0 -> 1802 | Runner serial de modulos explicitamente nombrados; registra comando, stdout/stderr y exit code; verifica cada escritura. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\verification.log` | 0 -> 3096 | Comparacion de fallos original/final, hashes de fuentes y git diff --check. |

**process_lifecycle.py NO MODIFICADO:** `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py`, 181050 -> 181050 bytes, SHA-256 igual al inicial. Tampoco se modificaron steam_preflight.py ni build_native_launcher.py; hashes en verification.log. No se escribio en server.py, host_config.py, daemon_policy.py, control_client.py ni daemon.py. Sin git add, commit, stash, resellado ni despliegue. El indice ajeno no fue escrito.

## B - Resultado de las pruebas

Interprete exacto: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`. Todas las invocaciones unittest se ejecutaron con cwd `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools`, PYTHONPATH `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`, PYTHONDONTWRITEBYTECODE=1 y PYTHONIOENCODING=utf-8. El flag -B evita escrituras de bytecode. Los comandos siguientes son los invocados por run_tests.py, con stdout/stderr combinados. No se ejecuto discover ni la suite completa.

**Rojo definitivo ANTES del arreglo final (red.log):** se verifico el hash de la copia original, se restauro exclusivamente el fichero de esta lane, se ejecutaron los tests finales y se aplico el arreglo en finally, tras comprobar que no habia escritor intermedio.

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_db05_preflight_diagnostics -v
Ran 13 tests in 0.148s
FAILED (failures=17)
EXIT CODE: 1
```

Los 17 fallos son aserciones discriminantes; no errores de fixture. Las partes 1 y 2 fallan por separado: faltan preflight_skipped_checks, causa en require_extension_run y sobre de resultado con el estado. Los controles de rechazo repetido y de otros errores de extension ya pasan con original; no se presentan como tests discriminantes del arreglo.

**Verde (green.log):** 323 tests en 9 modulos, incluido el modulo nuevo y tests.test_dayz_test_tool. green.log es un extracto declarado, sin modificar lineas de salida; regression.log conserva tambien los tres modulos rojos.

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_db05_preflight_diagnostics -v
Ran 13 tests in 0.177s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_dayz_test_tool -v
Ran 62 tests in 0.359s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_dayz_test_tool_modes -v
Ran 17 tests in 0.198s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_dayz_test_value_error_codes -v
Ran 8 tests in 0.090s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_lote2_t2_steam -v
Ran 5 tests in 0.642s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_vpp_preflight -v
Ran 73 tests in 3.003s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_box_occupancy -v
Ran 81 tests in 1.768s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_lote_v_products -v
Ran 13 tests in 2.682s
OK
EXIT CODE: 0
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_mcp_tools -v
Ran 51 tests in 11.506s
OK
EXIT CODE: 0
```

**Regresion adicional que NO esta verde (regression.log):**

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_build_native_launcher_policy -v
Ran 30 tests in 0.060s
FAILED (errors=1, skipped=1)
EXIT CODE: 1
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_lifecycle_reconcile -v
Ran 94 tests in 2.159s
FAILED (failures=1, errors=1)
EXIT CODE: 1
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B -m unittest tests.test_request_path_authority -v
Ran 13 tests in 0.364s
FAILED (errors=10, skipped=2)
EXIT CODE: 1
```

**Contrafactual con original (original-regression.log):** cada modulo corre en un interprete nuevo. run_original.py ejecuta el fuente original, comprobado contra baseline.json, en el namespace del modulo antes de cargar los tests. Las demas dependencias siguen siendo las del arbol vivo; esto aisla este diff, no reconstruye todo HEAD.

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\run_original.py' tests.test_build_native_launcher_policy -v
Ran 30 tests in 0.065s
FAILED (errors=1, skipped=1)
EXIT CODE: 1
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\run_original.py' tests.test_lifecycle_reconcile -v
Ran 94 tests in 1.825s
FAILED (failures=1, errors=1)
EXIT CODE: 1
```

```text
& 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe' -B 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\db05-2026-09-08\run_original.py' tests.test_request_path_authority -v
Ran 13 tests in 0.161s
FAILED (errors=10, skipped=2)
EXIT CODE: 1
```

Comparacion mecanica: las 13 identidades `ERROR:`/`FAIL:` son exactamente iguales con original y arreglo (verification.log). Los fallos son: uno de acreditacion de rutas en test_build_native_launcher_policy; diez de acreditacion en test_request_path_authority; un PermissionError leyendo closure-manifest.json y una asercion derivada de ese manifest ilegible en test_lifecycle_reconcile. No se atribuye la causa raiz de los errores de acreditacion sin aislarla.

**Calibracion previa conservada:** el primer gate uso el token de fixture mission_unavailable, que no pertenece a WORKER_ERROR_CODES. Se sustituyo por operation_cancelled (dayz_test_worker.py:601-602) y por vpp_preflight_failed para VPP (native_launcher_transaction.py:137), ANTES del rojo definitivo. No se cambio la implementacion para aceptar tokens inventados. Los intentos anteriores y sus resultados literales estan en red-initial-fixture-error.log, red-first-contract.log y green-first.log; no son la evidencia final.

```text
git diff --check -- tools/dayz_mcp/dayz_test_tool.py
[salida vacia]
EXIT CODE: 0
```

## C - Hallazgos y decisiones

1. **Salida (b), autorizada por el brief.** El comportamiento de saltarse Steam existe y ademas lo exige test_dayz_test_tool.py:1200-1262. La salida (a) cambiaria ese contrato y obligaria a editar un test existente fuera de la lista autorizada. El arreglo declara las omisiones sin cambiar su veredicto.

2. **Contrato final del campo.** `preflight_skipped_checks` aparece en TODOS los sobres de resultado de `execute_dayz_test_run` con preflight=true, incluido [] cuando no se omite ninguno de estos controles. Las salidas de VPP y del worker lo publican (dayz_test_tool.py:1410 y :1619). No se agrega a lanzamientos reales. Es un inventario de controles desactivados por preflight en este adaptador, no una lista de todo lo que no llego a ejecutarse tras un error anterior, ni una promesa de build/launch/readiness.

| Peticion valida de preflight | preflight_skipped_checks |
|---|---|
| mode=server, sin run_id | [] |
| mode=all, sin run_id | ["steam_session"] |
| mode=client, con run_id | ["steam_session", "extension_run", "client_replacement"] |

La matriz sigue siendo la autoridad existente; no se relaja run_id. `extension_run` comprende existencia/estado/proyecto del run y `client_replacement` la elegibilidad para sustituir al cliente segun proceso/bridge. Test nuevo :59 demuestra un preflight verde que declara las omisiones frente a un lanzamiento real rechazado por Steam, con el mismo proveedor stale; :80 cubre la lista vacia; :86 y :97 las salidas fallidas. Ni evaluar ni remediar Steam ocurre en preflight, aun si auto_remediate_steam=true.

3. **Steam es de lectura, pero el arreglo no lo ejecuta en preflight.** evaluate_steam_session esta definido en steam_preflight.py:211-246. Su provider solo lee registro, enumera procesos y consulta imagen/liveness (steam_preflight.py:75-134); la remediacion tiene otra funcion. Los dobles sugeridos no se definen en test_lote2_t2_steam.py: ese fichero los IMPORTA desde test_steam_preflight.py:282 y :316. Se reutiliza _MutableSteamProvider; no hace falta _FakeRemediationHost porque ninguna remediacion forma parte del cambio.

4. **run_not_extensible conserva token y causa.** require_extension_run guarda el estado observado en DayzTestToolError.cause (dayz_test_tool.py:315-328); sin string de estado util informa "unknown". execute_dayz_test_run captura solo ese codigo, devuelve status=failed, phase=validating, run_id original y run_not_extensible_cause con el estado (dayz_test_tool.py:1481-1507). El resto de errores conserva su contrato. Esto evita perder la causa en server.py:3535, que solo serializa error.code al convertir excepciones; los resultados dict se devuelven completos en server.py:3548. No se reconsulta el run para inventar la causa a posteriori.

No se clasifica el estado como transitorio o terminal: RUNNING puede tener otro propietario y UNRECONCILED admite recuperacion; ese string solo no justifica una politica de retry. Los tests cubren STARTING, RUNNING, STOPPING, EXITED y UNRECONCILED, ademas de estado ausente y RUNNING_IDLE admisible.

5. **No hay un tope de attaches que descontar en las rutas inspeccionadas.** El rechazo Steam retorna en dayz_test_tool.py:1475, antes de lifecycle_status (:1480) y antes del launcher. El test :169 ejecuta seis rechazos sobre el mismo runtime idle, verifica el run identico, cero lecturas lifecycle/bridge/reconcile y cero invocaciones launcher; tras recuperar ActiveUser, la septima peticion llega al launcher. Ese control ya pasa contra original. process_lifecycle.py:2245-2248 decide por estado != RUNNING, sin contador. RunRecord (:684-698) tampoco guarda intentos; _start_rejection (:1990-2010) audita/rechaza reserva. El bucle de reintentos hallado en :2800-2820 espera liberacion de puertos dentro de una operacion; no limita attaches sucesivos. El worker adopta antes del start (:671-674), por eso la puerta inferior espera RUNNING y la superior RUNNING_IDLE.

Este dato no identifica el estado del cuarto rechazo observado. Puede haber una transicion concurrente o temporal; eso queda [HIPOTESIS] sin evidencias del run real. El test modela un runtime idle y auto_remediate_steam=false; _require_idle_session puede reconciliar estado local pendiente ANTES de la puerta Steam (dayz_test_tool.py:440-464), asi que no se afirma que cualquier peticion imaginable carezca de efectos anteriores.

6. **Mejora Steam opcional NO aterrizada.** Se probo el mismo patron de bridge_status_cause usando _bridge_status_cause, pero test_dayz_test_tool.py:1139 compara el conjunto exacto de claves del sobre. El primer intento tambien agregaba el campo de omisiones a lanzamientos reales, rechazado por :844, :990 y :1069. Se retiro la mejora opcional y se acoto el campo nuevo a resultados preflight. Cambiar esos tests o la descripcion del parametro en server.py:3393 es FUERA DE MI ALCANCE. green-first.log conserva la evidencia; el codigo final mantiene el comportamiento anterior de las excepciones Steam.

7. **Correcciones al brief.** dayz_test_worker.py:547-550 ya no contiene la invariante citada: esta en :626-631, referida a resolver mission antes de retornar. Se corrigio el comentario demasiado general y el puntero obsoleto en el adaptador. La comprobacion de PACKAGED_MODULES (build_native_launcher.py:53-73) confirma que ninguno de los dos fuentes autorizados se incluye; este diff no necesita resellado.

## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO

La contradiccion observada es real: el adaptador omite Steam cuando preflight=true, y el test discriminante reproduce el verde mientras la misma peticion real falla. Pero la invariante del worker tiene un alcance mas estrecho que "esto va a lanzar": retorna inmediatamente despues de resolver mission y antes de build/adopt/start. Convertirla en garantia total seria incorrecto incluso consultando Steam, porque su estado puede cambiar entre comprobacion y lanzamiento. La salida (b) es una correccion de informacion; un consumidor que ignore el campo seguira viendo status=succeeded.

La sospecha de un tope de tres attaches no queda sustentada por estas rutas: los rechazos por Steam no llegan al lifecycle en el runtime idle probado, y la puerta real usa el estado. La secuencia temporal de tres errores Steam y un cuarto run_not_extensible no demuestra causalidad. Sin el estado y los eventos de aquel run no puede decidirse si hubo cambio de propietario, reconciliacion, salida de procesos u otra causa.

Hay otra tension verificable en el encargo: exige nuevos campos y al mismo tiempo tests existentes verdes sin permitir modificar esos tests. La opcion obligatoria se pudo acotar a preflight, y la opcional se retiro; no se falseo el conjunto de claves ni se parchearon asserts. La regresion ampliada tampoco partia verde en esta caja: sus mismos 13 fallos sobreviven al contrafactual original, de modo que no son evidencia contra este diff por si solos.

## E - LO QUE NO PUDE VERIFICAR

- TODOS los modulos relacionados verdes: tres modulos fallan tambien con el fuente original. Hay 13 identidades de fallo y 3 skips; sus logs y comandos estan en B. La causa raiz de los errores de acreditacion de rutas no se ha aislado. Arreglar request_path_authority, sus tests o el manifest es FUERA DE MI ALCANCE por la lista exclusiva de ESTE brief.
- Estado exacto del cuarto rechazo del usuario y comportamiento in-game: no se lanzo ni consulto el juego ni el daemon vivo; ESTE brief prohibe hacerlo. No se tocaron los pids ni el puerto 8765.
- Suite completa: prohibida expresamente por ESTE brief. Los modulos se ejecutaron uno a uno, con sus nombres. test_mcp_tools utiliza peers/servidor de loopback de test en puerto 0 (test_mcp_tools.py:138-148), no el daemon de produccion ni una herramienta MCP viva.
- Excepciones Steam con causa en el resultado: mejora opcional no aplicada por la incompatibilidad del test de claves exactas explicado en C. Se mantiene fail-closed con steam_session_stale, sin nuevo diagnostico para esa excepcion.
- Rechazo inferior de process_lifecycle.start_run tras una carrera entre admision y start: sigue teniendo su contrato anterior; este cambio captura el estado conocido en require_extension_run. No se modifica process_lifecycle.py porque no aparece el supuesto tope. Transportar mas detalle desde el worker requeriria estudiar/ampliar un contrato sellado fuera de estos ficheros.
- Recuperabilidad inferida por estado: no verificada ni prometida. El campo identifica la observacion y no indica reintentos automaticos.
- Errores de argumentos/launcher que se elevan como ToolError antes de producir un sobre no incorporan preflight_skipped_checks; tampoco pueden confundirse con un preflight verde. La garantia de presencia aplica a todos los sobres devueltos, no a excepciones.
- Actualizar doc publica de server.py, bugs compartidos, HANDOFF.md, vault, buzon pipeline_feedback o AI/30_Sessions: FUERA DE MI ALCANCE por ESTE brief y las raices de escritura. El cierre de post-session queda materializado en este STATE, con memoria de decisiones/evidencia para que Claude la integre.
- Revision independiente y commit: corresponden al receptor Claude segun el brief. No se abrieron subagentes ni se hizo fan-out, git add, commit, stash o build de launcher.
