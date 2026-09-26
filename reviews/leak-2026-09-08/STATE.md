## A - Ficheros creados/modificados

Tamaños comparados contra el inicio de esta lane (directorio de salida inicialmente inexistente). Todas las escrituras se hicieron en una pasada con Python y lectura inmediata de bytes para verificar igualdad; las fuentes se respaldaron antes de editar.

- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPCallbacks.c` — **1147 -> 1541 bytes**. Retorno débil a un bridge Managed y descarte por identidad después de Release; desconexión del cache al cerrar.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPBridge.c` — **81651 -> 82283 bytes**. Cache de poll sano, identidad nueva tras error/timeout y Managed; se conservan guard de admisión y Find/Remove.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPClientBridge.c` — **94351 -> 94919 bytes**. Mantiene su base MCPJobRunnerOwner; desconecta callbacks retirados/resultados/cache al cerrar y retira identidad fallida.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_leak_callback_lifetime.py` — **0 -> 7142 bytes**. Contratos de ownership, identidad, detachment y vida del singleton (7 tests).
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_leak_callback_drain.py` — **0 -> 8347 bytes**. Modelo enlazado a fuentes para drenaje/admisión y mutaciones negativas (8 tests).
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\after-sources.json` — **0 -> 765 bytes**. Hashes y tamaños entregados de las tres fuentes y los dos tests nuevos.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\baseline.json` — **0 -> 674 bytes**. Hashes y tamaños iniciales de las tres fuentes.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\compare_validator.py` — **0 -> 2312 bytes**. Compara diagnósticos del validador contra BEFORE usando lectura en memoria, sin revertir fuentes.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\DIAGNOSIS.md` — **0 -> 14452 bytes**. Asimetría, comparación de caminos, números, hipótesis nativa, cambios y límites con citas verificadas.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\green-partial-edit-results.json` — **0 -> 20144 bytes**. Metadatos de la corrida intermedia, sin ocultar su exit code 1.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\green-partial-edit.log` — **0 -> 93770 bytes**. Corrida previa con cliente aún sin editar: conserva los fallos de aquel estado intermedio.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\green-results.json` — **0 -> 21177 bytes**. Comandos, resúmenes y exit codes del verde final.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\green.log` — **0 -> 86465 bytes**. Salida literal de la corrida verde final, 34 módulos explícitos.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\MCPBridge.c.BEFORE` — **0 -> 81651 bytes**. Copia binaria previa e inmutable del servidor.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\MCPCallbacks.c.BEFORE` — **0 -> 1147 bytes**. Copia binaria previa e inmutable de callbacks.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\MCPClientBridge.c.BEFORE` — **0 -> 94351 bytes**. Copia binaria previa e inmutable del cliente.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\red-initial-results.json` — **0 -> 5386 bytes**. Metadatos del rojo inicial.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\red-initial.log` — **0 -> 119350 bytes**. Primera corrida roja directamente sobre las fuentes originales antes de modificarlas.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\red-results.json` — **0 -> 7192 bytes**. Comandos, resúmenes y exit codes del rojo final.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\red.log` — **0 -> 124870 bytes**. Salida literal de los tests finales contra los BEFORE acreditados por SHA.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\run_checks.py` — **0 -> 3585 bytes**. Runner reproducible que nombra módulos explícitos, en serie; guarda salida y verifica cada escritura.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\source.patch` — **0 -> 6216 bytes**. Diff de las tres fuentes para revisión; los nuevos tests se entregan como archivos.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\validator-before.log` — **0 -> 4293 bytes**. Salida baseline y comparación: mismos diagnósticos; comparación exit 0, baseline exit 1.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\validator.log` — **0 -> 4466 bytes**. Salida literal JSON del validador estructural adicional; FAIL previo, 2 errores y 5 avisos.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\STATE.md` — **0 -> 26575 bytes**. Informe final A-E con inventario, comandos, resultados y asuntos no verificados.
- `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08\DONE` — **0 -> 0 bytes**. Marcador vacío de entrega; no significa PASS in-game.

## B - Resultado de las pruebas

La prueba del motor no se ejecutó. Resultado final offline: **403 tests, 34 módulos, 34 exit codes 0**. Son los 32 módulos existentes localizados por contenido y los dos nuevos test_leak_. No se ejecutó discover ni la suite completa.

Invocaciones reales del runner desde la raíz del repo:

```powershell
& 'tools/.venv-mcp/Scripts/python.exe' -B 'reviews/leak-2026-09-08/run_checks.py' red
& 'tools/.venv-mcp/Scripts/python.exe' -B 'reviews/leak-2026-09-08/run_checks.py' green
```

El runner lanza cada módulo por separado, mediante subprocess sin shell. A continuación se copia la línea de comando que Windows recibe para cada subproceso, su resumen literal y su exit code. Cwd de TODOS los subprocesos: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools`. Entorno: `PYTHONPATH=C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONIOENCODING=utf-8`. En rojo: `DAYZ_LEAK_BEFORE_DIR=C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\leak-2026-09-08`; en verde esa variable se elimina. Los cuerpos completos de stdout/stderr están en red.log y green.log.

Rojo final (runner exit 1; copias originales verificadas, fuentes de trabajo no revertidas):

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_leak_callback_lifetime -v
Ran 7 tests in 0.019s
FAILED (failures=22)
EXIT CODE: 1
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_leak_callback_drain -v
Ran 8 tests in 0.550s
FAILED (failures=2)
EXIT CODE: 1
```

Extracto literal que discrimina el problema de asignación (`red.log:282`):

```text
AssertionError: 3420 != 1 : one native RestCallback allocation per poll amplifies native retention
```

Verde final (runner exit 0):

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_leak_callback_lifetime -v
Ran 7 tests in 0.020s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_leak_callback_drain -v
Ran 8 tests in 0.563s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_action_use -v
Ran 3 tests in 0.099s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_addon_tree_has_no_write_artifacts -v
Ran 5 tests in 0.002s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_batch6 -v
Ran 13 tests in 0.143s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_bridge_client_capabilities -v
Ran 8 tests in 0.003s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_bridge_server_capabilities -v
Ran 9 tests in 0.013s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_d09_d10_spawn_timeout_object_id -v
Ran 2 tests in 0.022s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_effective_schema_catalog -v
Ran 5 tests in 0.002s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_entities_has_cargo -v
Ran 5 tests in 0.005s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_entities_query_cargo -v
Ran 12 tests in 0.356s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_guards_bridge -v
Ran 8 tests in 0.072s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_instance_fence -v
Ran 64 tests in 1.990s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_inventory_give -v
Ran 5 tests in 0.093s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_key_press -v
Ran 6 tests in 0.167s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_loopback -v
Ran 74 tests in 0.969s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_mcp_tools -v
Ran 51 tests in 7.872s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_object_anim -v
Ran 4 tests in 0.088s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_object_inspect -v
Ran 4 tests in 0.089s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_player_respawn -v
Ran 4 tests in 0.090s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_player_teleport -v
Ran 9 tests in 0.441s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_poll_key_reload_contract -v
Ran 2 tests in 0.001s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_poll_watchdog_contract -v
Ran 8 tests in 0.003s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_restore_gameplay_contract -v
Ran 12 tests in 0.610s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_surface_query -v
Ran 4 tests in 0.085s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_task9_spawn_phase_markers -v
Ran 7 tests in 0.001s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_ui_click_scriptview -v
Ran 6 tests in 0.002s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_ui_enforce_contract -v
Ran 15 tests in 0.008s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_ui_error_diagnostics -v
Ran 10 tests in 0.598s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_ui_reload_layout -v
Ran 10 tests in 0.075s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_vehicle_prepare_fixture -v
Ran 4 tests in 0.104s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_vehicle_telemetry_contract -v
Ran 10 tests in 0.027s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_vehicle_trace_contract -v
Ran 7 tests in 0.095s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_world_spawn_ground_contract -v
Ran 2 tests in 0.001s
OK
EXIT CODE: 0
```

La prueba de 3.420 polls verifica un objeto por secuencia de éxitos y array vacío después de cada terminación. El guard se mantiene exactamente `Count(callbacks) + Count(pending) + Count(jobs) > 128 - 64`. El positivo con 65 resultados pendientes cierra admisión y la reabre tras un ACK. Las dos mutaciones en memoria (quitar Release y quitar Remove) dejan 65 holds y cierran admisión: esos controles negativos también se ejecutaron. El modelo no implementa la gestión de memoria nativa de Enforce.

Corridas intermedias conservadas:

- `red-initial.log`: misma invocación de runner red, antes de añadir la selección BEFORE; se verificó entonces que las tres fuentes de trabajo eran idénticas a sus copias. Salida literal: `Ran 6 tests in 0.019s`, `FAILED (failures=16)`, `EXIT CODE: 1`; y `Ran 8 tests in 0.654s`, `FAILED (failures=2)`, `EXIT CODE: 1`.
- `green-partial-edit.log`: misma invocación green, iniciada después de que un reemplazo exacto rechazara la declaración del cliente. Salida del módulo de lifetime: `Ran 6 tests in 0.021s`, `FAILED (failures=5)`, `EXIT CODE: 1`; runner exit 1. La corrida final anterior reemplaza este estado como veredicto de entrega.

Validador estructural adicional, invocación real del subproceso desde la raíz:

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B C:/Users/guill/DayZ-Modding-Knowledge-Pack/tools/dayz-script-validator/scripts/script_validator.py "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon"
```

Campos literales de su JSON en validator.log y exit code:

```text
  "status": "FAIL",
EXIT CODE: 1
```

Son 2 errores y 5 avisos. Se comprobó el baseline sin copiar ni revertir el addon: el helper sustituye solo la función de lectura de las tres fuentes por sus BEFORE, dejando el mismo resto del árbol.

```powershell
& 'tools/.venv-mcp/Scripts/python.exe' -B 'reviews/leak-2026-09-08/compare_validator.py'
```

Salida literal:

```text
BEFORE validator: FAIL
errors=2 warnings=5
BASELINE VALIDATOR EXIT CODE: 1
CURRENT validator: FAIL
errors=2 warnings=5
SOURCE/SEVERITY/RULE FINDINGS IDENTICAL: True
COMPARISON EXIT CODE: 0
```

Comprobación de whitespace:

```text
git diff --check -- addon/scripts/5_Mission/MCPCallbacks.c addon/scripts/5_Mission/MCPBridge.c addon/scripts/5_Mission/MCPClientBridge.c tools/tests/test_leak_callback_lifetime.py tools/tests/test_leak_callback_drain.py
(sin salida)
EXIT CODE: 0
```

## C - Hallazgos y decisiones

- La diferencia es asignación por petición (servidor original) frente a reutilización (cliente). Ambos ReleaseCallback tienen camino de eliminación; no encontró la supuesta rama que libera en un lado y no en el otro. Citas de ambos lados, por etapa, en DIAGNOSIS.md.
- Un callback que salió del array ya no completa el ciclo hipotetizado. Su supervivencia necesita otra raíz, otra lógica desplegada o una anomalía nativa. El reporte primario histórico T188795 hace plausible una retención de RestCallback; no demuestra el mecanismo ni su vigencia en este binario. Se implementa el acotamiento observado y se deja la causa nativa como HIPÓTESIS.
- No se conocen los polls reales. El intervalo de 0,2 s se acumula fuera de in-flight, hay idle registrado y el cliente empieza después. No se inventa una fracción de callbacks liberados a partir de 2.214/3.420.
- No hay líneas de resultados enviados/confirmados en la corrida citada: la ausencia de MCPResultCallback fugados no sirve como control de POST. Se ha protegido también su relación con el bridge, sin crear un pool de resultados.
- Corrección propia durante la implementación: el primer contrato daba por hecho que podría hacer Managed directamente al cliente. La edición exacta rechazó la declaración real `extends MCPJobRunnerOwner`, antes de tocar ese fichero. Leí su base, que no es Managed, y corregí contrato e implementación. Cambiar MCPJobRunner.c está FUERA DE MI ALCANCE; el cliente conserva ref con desconexión explícita. La corrida intermedia fallida y el rojo inicial se conservan.
- El caso latente concreto del cliente es un poll cacheado que ya terminó y salió de m_PollCallbackRefs: los bucles de Shutdown no lo desconectaban. Se desconecta expresamente el cache además de las listas. Se desconectan asimismo callbacks de resultados/identidades retiradas al terminar.
- Se retira identidad tras error/timeout porque la API documenta OnError repetible; respuestas de generaciones antiguas se drenan y descartan. Esto puede crear un objeto por recuperación, aunque una secuencia de éxitos crea solo uno. La liberación nativa residual por recuperaciones, watchdog o misiones sigue sin demostrar.
- Se mantiene el guard da3b75f y el drenaje por petición. No se cambia versión/protocolo, jobs, dispatcher, frecuencia, backoff ni URLs. Sin N3, logs nuevos, cambios Python de producto, despliegue ni resellado.
- Censo corregido: había 32 módulos ejecutables pertinentes más _addon_paths.py, no 34 existentes. Con los dos nuevos se han ejecutado 34, por nombre y en serie.
- Se encontró un validador externo aunque el brief indicaba que no había linter aquí. Sus dos errores preexistentes, ES-LAYOUT-PATH-PBOPREFIX-MISMATCH y ES-LAYOUT-FILE-MISSING, son sobre sondas opcionales de MCPDialogController.c:39-40. Se leyó el sondeo/fallback de :166-187. Los cinco avisos ES-GETTYPE-EXACT-MATCH señalan código intacto. Los diagnósticos BEFORE/AFTER coinciden; corregir esas rutas/avisos es FUERA DE MI ALCANCE. El gate bruto permanece FAIL; no se declara PASS global.
- P: no está accesible y el sibling no coincide con los bridges actuales. No se acredita el hash desplegado históricamente; no se diagnostica como hecho una divergencia host/sandbox ni se toca el despliegue.
- No hubo git add, commit ni stash. Motivo: prohibición del brief y revisión por Claude pendiente. HEAD permanece da3b75fc8e70f8720aaef56331932d624d3bc461. No se han usado herramientas DayZ MCP ni adquirido lease: no se gestionó ni mutó juego/daemon alguno. Tampoco se llamó session_status, expresamente prohibido por el alcance de herramientas de este brief.
- No hace falta resellado del bundle nativo por este diff: ninguno de los ficheros cambiados pertenece a PACKAGED_MODULES, leído en tools/build_native_launcher.py:53-72. El receptor tendrá que desplegar el addon que decida probar; esta lane no lo hizo.
- Cierre con post-session limitado a este directorio. Vault, project-brief, HANDOFF.md global, 30_Sessions y buzón pipeline_feedback están FUERA DE MI ALCANCE por la lista exclusiva. La memoria durable de esta lane es DIAGNOSIS.md y este STATE.md; no se escribieron fuera del alcance ni se enviaron mensajes a otras sesiones.

Observaciones para gastar la prueba in-game junto con las demás (no ejecutada ni solicitada aquí):

1. Acreditar los hashes del addon realmente cargado usando after-sources.json y la ruta del despliegue real.
2. Mantener varios minutos de polls exitosos y **comprobar que el polling NO se detiene**, especialmente mucho después de los ~13 s citados en el brief.
3. Medir GET efectivamente enviados y callbacks/generaciones creados, sin sustituirlos por Hz × duración; observar entradas pendientes del array si se instrumenta la corrida.
4. Producir y confirmar al menos un resultado de servidor y otro de cliente, para tener un control de POST que no existe en el log original.
5. Observar recuperación de error/timeout y respuesta tardía: una generación retirada no debe liberar el hold, despachar ni cambiar in-flight de la generación nueva.
6. Cerrar una misión entre polls y otra con petición pendiente; comparar los avisos de MCPBridge/MCPClientBridge y callbacks. Un callback residual constante confirma acotamiento, no eliminación de la fuga nativa. Sin desaparición de los avisos no dar el bug de motor por cerrado.

- Hallazgo de entorno en el cierre: la entrada de PowerShell por pipe usaba una codificación que convertía caracteres no ASCII en `?` antes de llegar a Python. La comparación write/read de los mismos bytes no lo detectaba. La lectura host de los informes reveló el fallo; un probe con escapes Unicode aisló la causa y pasó al fijar `$OutputEncoding` a UTF-8. Se repararon y releyeron íntegros los dos Markdown; los hashes de fuentes, BEFORE y resultados no cambiaron. No fue un truncamiento de OneDrive. Promoción de este hallazgo al buzón: FUERA DE MI ALCANCE.

## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO

El problema observado existe como aviso de limpieza de módulo, pero «mismo código» confunde una declaración igual con un patrón de asignación distinto. No se ha demostrado que el servidor acumule callbacks en su array: Release y Shutdown quitan esas referencias en el código disponible. El dato 2.214 no exige una población de 3.420 ni una fracción de liberación selectiva. Tampoco la ausencia de callbacks de resultado exonera ese camino si no se emitieron resultados.

Mi arreglo puede acotar un síntoma de retención nativa sin corregir su causa. Es deliberado: el motor es opaco y está prohibido probarlo. No sería honesto dar como resuelta toda la ficha con tests Python. La hipótesis alternativa de diferencias en lo realmente desplegado sigue abierta porque no se ha acreditado el hash histórico. Otra diferencia puramente nativa de retención/contabilidad entre GET/POST, o entre creación y ejecución, requiere un experimento aislado en el mismo binario; los tests de fuentes no lo reemplazan.

Cambiar Managed al padre del cliente no era una edición autorizada ni necesaria para romper los pins identificados: desconectar explícitamente conserva su jerarquía. El refactor N3 no está justificado por estos datos y tampoco demostraría que el motor destruya RestCallbacks. El guard de admisión es útil y se conserva; retirarlo escondería una acumulación del array si apareciera, sin corregir ownership.

## E - LO QUE NO PUDE VERIFICAR

- Causa raíz nativa de los 2.214 callbacks, conteo real de GET y fracción destruida: los logs no llevan esos contadores y GET/POST son proto sin implementación C++ local. Se deja HIPÓTESIS/INCONCLUSO.
- Desaparición de avisos de fugas, refcounts, RSS y continuidad del polling en el motor: lanzar o tocar DayZ/DayZDiag y usar tools MCP está prohibido por ESTE brief. El modelo offline no sustituye esa ejecución.
- Compilación Enforce y comportamiento del nuevo Managed/soft link en ese binario: no se ejecutó el compilador del juego; el validador estructural no es un compilador. Restricción de ESTE brief sobre juego/daemon.
- Cantidad y orden efectivos de notificaciones nativas: solo está documentado que OnError puede repetirse. La reutilización sana presupone una única terminación de éxito por GET completado; el motor no se ejercitó. Se protege explícitamente la generación tras error/timeout y el abandono del cliente.
- Eliminación de todo callback nativo residual tras errores/timeouts/watchdog, POSTs o cambios de misión: se acota la asignación por poll exitoso; estas otras generaciones pueden seguir retenidas si la hipótesis nativa es cierta.
- Hash de lo cargado en la corrida de las 15:06: P: no está disponible y los logs no lo incluyen. El sibling es solo candidato; no se acredita como despliegue. No se desplegó nada por los límites de ESTE brief.
- Suite completa y prueba del daemon de producción: expresamente prohibidas por ESTE brief. Solo los 34 módulos listados se ejecutaron; sus fixtures de loopback utilizan puertos de test, sin contactar 8765 ni arrancar DayZ.
- Gate estructural global verde: el validador da FAIL con dos errores y cinco avisos idénticos en BEFORE y AFTER. Corregir MCPDialogController.c o lógica GetType ajena es FUERA DE MI ALCANCE por ESTE brief.
- Revisión independiente por Claude, commit y publicación: pendientes del receptor; este encargo prohíbe subagentes, git add y commit.
- Actualizaciones del vault y pipeline_feedback: FUERA DE MI ALCANCE por la lista exclusiva de ESTE brief. Se deja todo lo relevante en este directorio, sin pedir confirmación.
