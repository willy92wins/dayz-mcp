## A - Ficheros creados/modificados

Entrega: llamada nativa identificada y eliminada del bridge. Corrección en árbol de trabajo; ejecución Enforce todavía INCONCLUSA.

Tamaños respecto al inicio de esta lane; 0 antes indica archivo inexistente. Las carpetas de salida y test-tmp eran nuevas; test-tmp ha quedado vacía.

| Ruta absoluta | Bytes antes -> después | Cambio |
|---|---:|---|
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPClientBridge.c` | 94919 -> 95702 | Elimina GetCurrentCamera; disponibilidad común, lectura de m_ActiveCam y rechazo explícito también en SETTLE. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_camera_native_crash.py` | 0 -> 8724 | 12 tests nuevos: contrato de fuente y consumidor real de restore con transporte simulado. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\apply_camera_fix.py` | 0 -> 3755 | Edición de una pasada; rechaza cambios concurrentes y verifica bytes. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\DIAGNOSIS.md` | 0 -> 13532 | Causa nativa, citas reabiertas, hipótesis refutada, cambio y límites. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\DONE` | 0 -> 0 | Marcador vacío de entrega escrita y verificada. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\finalize_report.py` | 0 -> 21290 | Consolida evidencia por una pasada, calcula tamaño propio de STATE y escribe DONE al final. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\green-results.json` | 0 -> 6722 | Índice de comandos, exit codes y resúmenes literales de los módulos verdes. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\green.log` | 0 -> 66002 | Salidas literales de los 21 módulos, ejecutados en serie. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\inspect_minidumps.py` | 0 -> 3406 | Parser reproducible offline; no adjunta un debugger ni modifica evidencia. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\MCPClientBridge.c.BEFORE` | 0 -> 94919 | Copia exacta previa a esta lane; 94.919 bytes, verificada por hash. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\minidumps.json` | 0 -> 1733 | Extracción acotada de excepciones, registros, módulo, bytes y hashes originales. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\red-results.json` | 0 -> 2141 | Índice de comando, exit code y resumen literal del control rojo. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\red.log` | 0 -> 14754 | Salida literal del módulo nuevo contra la copia previa: 9 fallos esperados. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\run_checks.py` | 0 -> 3255 | Runner serial con módulos explícitos, temporales confinados y relectura por tramo. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\run_validator.py` | 0 -> 2285 | Captura offline de baseline, bridge final y addon completo. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\STATE.md` | 0 -> 22801 | Este informe A-E, con comandos, tamaños y pendientes concretos. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\validator-addon.json` | 0 -> 4172 | Salida literal del validador sobre el addon completo, incluidos hallazgos ajenos. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\validator-after.json` | 0 -> 1315 | Salida literal del validador sobre el bridge corregido. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\validator-before\MCPClientBridge.c` | 0 -> 94919 | Copia .c del baseline para que el validador acepte la extensión. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\validator-before.json` | 0 -> 1340 | Salida literal del validador sobre el baseline. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\validator-results.json` | 0 -> 1634 | Comandos y exit codes del validador; el JSON completo contiene info.files_scanned. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\validator.log` | 0 -> 7914 | Comandos, salidas completas y exit codes de las tres validaciones. |
| `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\verification.log` | 0 -> 617 | Auditoría final de alcance, hashes, cobertura, dumps e índice solo leído. |

No se hizo git add, commit, stash, despliegue, resellado ni gestión de procesos. El índice sigue mostrando únicamente decisions/decision-log.md; no se ha escrito. Los cambios ajenos iniciales en native-launchers se dejaron intactos.

## B - Resultado de las pruebas

Comandos literales de los procesos de test. Los wrappers run_checks.py red/green los ejecutaron uno por uno; no hubo discover ni suite completa. Salida íntegra en red.log y green.log.

Entorno común de cada módulo:
```text
cwd=C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools
PYTHONPATH=C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev
PYTHONDONTWRITEBYTECODE=1
PYTHONUTF8=1
TEMP=C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\test-tmp
TMP=C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\test-tmp
```

Control rojo: MCP_CAMERA_SOURCE apunta a .BEFORE; no se revirtió el archivo compartido.
```text
MCP_CAMERA_SOURCE=C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\MCPClientBridge.c.BEFORE
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_camera_native_crash -v
Ran 12 tests in 0.031s
FAILED (failures=9)
EXIT CODE: 1
```

Las 9 trazas completas del rojo están en red.log; una de ellas prohíbe expresamente el getter que figura en las dos pilas. Las otras incluyen guards ausentes y el fallback que declaraba cámara del jugador.

Fuente final: MCP_CAMERA_SOURCE eliminado del entorno. Los resúmenes siguientes son literales, sin normalizar duraciones.

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_camera_native_crash -v
Ran 12 tests in 0.522s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_action_use -v
Ran 3 tests in 0.109s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_bridge_client_capabilities -v
Ran 8 tests in 0.005s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_addon_tree_has_no_write_artifacts -v
Ran 5 tests in 0.004s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_effective_schema_catalog -v
Ran 5 tests in 0.003s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_guards_bridge -v
Ran 8 tests in 0.093s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_instance_fence -v
Ran 64 tests in 1.279s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_leak_callback_lifetime -v
Ran 7 tests in 0.016s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_key_press -v
Ran 6 tests in 0.189s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_loopback -v
Ran 74 tests in 0.971s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_mcp_tools -v
Ran 51 tests in 8.778s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_player_respawn -v
Ran 4 tests in 0.095s
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
Ran 12 tests in 0.645s
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
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_ui_error_diagnostics -v
Ran 10 tests in 0.669s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_ui_enforce_contract -v
Ran 15 tests in 0.010s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_vehicle_telemetry_contract -v
Ran 10 tests in 0.035s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_ui_reload_layout -v
Ran 10 tests in 0.083s
OK
EXIT CODE: 0
```

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B -m unittest tests.test_vehicle_trace_contract -v
Ran 7 tests in 0.093s
OK
EXIT CODE: 0
```

Total aritmético: 21 módulos, 327 tests, todos exit 0. Se comprobó que la lista coincide con TODOS los módulos que nombran MCPClientBridge en tools/tests/*.py.

Validador estructural: comandos literales y fragmento literal del veredicto JSON; salida completa en validator.log y validator-*.json. Cwd = raíz del repo, con bytecode desactivado. No es un compilador Enforce.

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B C:\Users\guill\DayZ-Modding-Knowledge-Pack\tools\dayz-script-validator\scripts\script_validator.py "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\camera-crash-2026-09-08\validator-before\MCPClientBridge.c"
  "status": "WARN",
EXIT CODE: 2
```
1 fichero(s) leído(s); 0 errores, 2 avisos.

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B C:\Users\guill\DayZ-Modding-Knowledge-Pack\tools\dayz-script-validator\scripts\script_validator.py "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon\scripts\5_Mission\MCPClientBridge.c"
  "status": "WARN",
EXIT CODE: 2
```
1 fichero(s) leído(s); 0 errores, 2 avisos.

```text
"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" -B C:\Users\guill\DayZ-Modding-Knowledge-Pack\tools\dayz-script-validator\scripts\script_validator.py "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\addon"
  "status": "FAIL",
EXIT CODE: 1
```
11 fichero(s) leído(s); 2 errores, 5 avisos.

El bridge conserva los mismos 2 WARN ES-GETTYPE-EXACT-MATCH (2026 y 2739 -> 2026 y 2746). El addon completo da FAIL por dos rutas de hot layout de MCPDialogController.c:39-40, más 5 WARN GetType. Son hallazgos fuera del diff de cámara; no se declara PASS global.

Comprobación final de whitespace (salida literal vacía):
```text
git diff --check -- addon/scripts/5_Mission/MCPClientBridge.c
EXIT CODE: 0
```

Auditoría adicional: verification.log compara byte por byte todo lo que queda fuera de las dos funciones de cámara y el helper nuevo, verifica el hash de la revisión probada y relee ambos minidumps sin cambios.

## C - Hallazgos y decisiones

- Causa identificada: Camera.GetCurrentCamera() falla dentro del nativo, antes de devolver al script. Ambos RPT completos contienen BuildCameraResult:3664 y DispatchCameraGet:902; en reviews/guards-2026-09-08/MCPClientBridge.c.BEFORE:3664 está el getter. Los dos dumps dan RAX=0, lectura de 0x68 en DayZDiag_x64.exe+0x4f7910 (mov rcx,[rax+0x68]). La hipótesis de GetTransform sobre un puntero devuelto obsoleto no corresponde a estas pilas.
- Corrección al brief: la primera ocurrencia también pasó por DispatchCameraGet. Ambas incluyen un frame más profundo que el fragmento transcrito. El RPT registra ACCESS_VIOLATION nativa con ENGINE Crashed y minidump; no es solo una excepción VM recuperable. Las citas del código actual y los 93.901 bytes del snapshot desplegado sí coincidieron.
- IsClientInGame solo comprueba GetGame y GetPlayer (bridge:676-678). Un jugador sentado satisface ese predicado. Vanilla camera.c:3-7 documenta null para cámara del jugador, pero el getter murió antes de retornar; añadir if (!current) después no evita nada.
- Se usa m_ActiveCam ya declarado, más IsActive documentado y utilizado en vanilla con comprobación previa de referencia. El estado con comando de vehículo se rechaza; también cualquier padre, por separado, sin atribuirle un tipo no observado. Esto cubre el desacuerdo command/parent sin intervenir vehicle_get_in_client. La eliminación del getter es general y no depende de acertar si había un asiento.
- Error explícito y arrays vacíos para client_not_in_game, camera_unavailable_player, camera_unavailable_vehicle, camera_unavailable_parented_player, camera_unavailable_no_scripted_camera y camera_unavailable_inactive. Los valores escalares quedan por defecto en un bloque ok=false; no son mediciones válidas. Ambos llamantes mantienen el sobre protocolario existente result.ok=true y el fallo anidado camera.ok=false.
- El helper también evita la consulta global de interpolación durante SETTLE en estados rechazados, y deja que REPORT produzca el mismo bloque camera.ok=false. Esa llamada adicional no se atribuye como causa medida de los dos crashes. No se añadieron logs de tick ni nuevos miembros/mensajes.
- Degradación deliberada: restore_gameplay ejecuta cleanup, pero después de ReleaseCamera no hay referencia MCP verificable. El consumidor Python real devuelve restore_unverified también en pie. Reintentar no recuperará una observación ausente; no se ha falseado player_camera_active para hacerlo verde. También deja de observarse una cámara perteneciente a otro mod o la vista del jugador mediante camera_get.
- camera_set conserva sus efectos anteriores a REPORT (puede suprimir controles antes de informar cámara no verificable). Este parche no reconstruye ni toma el control de la cámara del vehículo. ReleaseCamera/RestoreGameplay conservan todos sus bytes, igual que guards y ownership de hoy; no hubo conflicto con da3b75f/0818ebd.
- Validador global: ES-LAYOUT-PATH-PBOPREFIX-MISMATCH y ES-LAYOUT-FILE-MISSING en MCPDialogController.c:39-40. Son rutas de sondeo opcional, comprobadas con FileExist antes de CreateWidgets en :169-173. Corregir el validador o esa UI está FUERA DE MI ALCANCE; no se tocó el archivo ni se atribuyó el FAIL a cámara.
- Corrección propia durante cite-then-verify: GetTransform admite arrays de 1 a 4 vectores (enentity.c:274-288); se usa 4 para matriz completa. El primer tramo del diagnóstico decía que siempre exigía 4 y quedó corregido. Una comprobación auxiliar python -c perdió comillas por el transporte de PowerShell; se repitió por stdin, verificando tamaño, contenido y hash. No hubo truncamiento observado en esta lane.
- No hace falta resellar el launcher: no se tocó ningún PACKAGED_MODULES de tools/build_native_launcher.py:53-72. La corrección está solo en fuente; no se ha sincronizado al árbol desplegado ni empaquetado PBO.
- Memoria durable: DIAGNOSIS.md y este STATE.md son el handoff autorizado al receptor Claude. Vault/30_Sessions, HANDOFF.md del proyecto, bug-ledger y pipeline_feedback están FUERA DE MI ALCANCE por la lista exclusiva del brief. No se escribieron ni se invocaron tools MCP para actualizarlos. Tampoco se hizo lease/session_status por la prohibición expresa de esta corrida.

Observaciones para integrar en la prueba conjunta ya prevista (sin solicitar otra corrida):

1. Verificar que el .c desplegado coincide con SHA-256 9ce3ec0bb1430dea4f91e15e5efa4fffb0d77d95570da259b8463ec4c5017a19 y que el motor acepta el módulo Mission.
2. Con cámara MCP activa en pie, comprobar camera_set/lookat y camera_get: snapshot utilizable y coincidencia con el viewport real. Incluir free/orient/matrix en el smoke si ya estaban previstos.
3. Sentado antes de la petición, y entrando en vehículo durante SETTLE: esperar camera.ok=false con camera_unavailable_vehicle o camera_unavailable_parented_player, viewport_moved=false y arrays vacíos. Ningún camera.ok=true solo por existir m_ActiveCam.
4. Repetir el orden camera_set -> restore_gameplay -> camera_get de ambos runs y confirmar supervivencia del cliente, sin nuevo ACCESS_VIOLATION/minidump ni timeout causado por desaparición del proceso.
5. Tras restore, comprobar manualmente vista, controles, HUD y simulación; esperar restore_unverified (el cleanup se ejecuta pero no se certifica). Anotar si el resultado es no_scripted_camera o vehículo/parentado según el estado.
6. Comprobar cámara MCP desactivada/reemplazada y camera_get sin haber creado una: fallo explícito, sin datos fabricados. Esto valida los nativos restantes y el supuesto operativo de IsActive; no quedó probado offline.

## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO

El problema real está confirmado, pero no queda probado que sea exclusivo de estar sentado: ambos fallos ocurren en una llamada nativa que debería poder devolver null según vanilla, y restore_gameplay libera la cámara justo antes de consultarla. Puede ser la ausencia del objeto de cámara tras ReleaseCamera, una transición de cámara de jugador, o un efecto de la combinación de mods/versión 1.29.163709. Dos runs con la misma combinación y secuencia no aíslan esos factores. El tipo y el dueño del objeto interno nulo no se deducen de RAX=0.

El brief orientaba hacia una referencia obsoleta devuelta al script; esa orientación habría colocado el guard demasiado tarde. La evidencia más fuerte es el frame completo y los registros, que permiten eliminar la llamada concreta sin adivinar ese ciclo de vida. El rechazo adicional de vehículo/parentado es una decisión conservadora frente a la observación de viewport sobreescrito, no prueba de que el asiento causase el acceso nulo.

El arreglo tiene un coste funcional serio: renuncia a observar cámaras ajenas/al jugador y deja restore_gameplay sin verificación positiva tras liberar. Puede que el producto prefiera recuperar esa capacidad mediante otra vía nativa validada en motor; eso no justifica conservar el getter que murió ni considerar ausencia como observación. Esta entrega corrige la dependencia peligrosa y documenta la degradación; no afirma equivalencia funcional completa ni crash-free demostrado.

## E - LO QUE NO PUDE VERIFICAR

- Compilación Enforce: no hay compilador disponible según este brief, y el validador es textual; no resuelve todos los miembros/tipos ni ejecuta código. Se abrieron declaraciones y herencias reales de los símbolos usados, pero eso no es una compilación.
- Reproducción en motor y supervivencia del cliente con el diff: restricción explícita de ESTE brief, que prohíbe lanzar DayZ/DayZDiag, tocar la sesión o usar tools MCP. No se ha ejecutado una reproducción nueva ni se pide una aquí.
- Seguridad universal de Camera.IsActive, GetCurrentFOV, IsInterpolationComplete y getters de instancia restantes: el C++ no está disponible con símbolos ni se hizo experimento en motor. La llamada que aparece en las dos pilas sí ha desaparecido del código del bridge.
- Tipo/ciclo de vida exacto del objeto interno nulo: los dumps y bytes muestran el acceso, no el nombre del campo +0x68 ni quién lo retiró. No se presenta como hecho la cámara obsoleta ni la sobreescritura por vehículo como causa aislada.
- Que el usuario estuviera sentado en ambos instantes y que el archivo desplegado sea byte a byte el snapshot: la observación procede de la otra sesión; el mapeo exacto de frames respalda la versión pero los dumps no incluyen un hash de scripts. No se inspeccionó el juego vivo por la prohibición del brief.
- Paridad de los scripts vanilla locales con el ejecutable de los dumps: firmas y usos locales sí abiertos; no hay prueba de correspondencia exacta con 1.29.163709.
- Retorno efectivo de controles, HUD, simulación y cámara tras restore: el consumidor solo confirma lo que puede observar; tras este cambio devuelve restore_unverified. No se reparó una API de verificación nueva ni la cámara de vehículos.
- Suite completa, daemon en 8765, despliegue, PBO y bundle: no ejecutados por restricciones explícitas de ESTE brief. Solo tests nombrados y procesos temporales offline; ningún proceso de producción gestionado. No se necesita resellado por los ficheros modificados.
- Certificación global del addon: el validador devuelve dos FAIL fuera del cambio de cámara. Resolverlos y editar sus archivos excede la lista de ficheros escribibles de ESTE brief.
- Registro de memoria/commit y revisión de otra familia: no realizados por esta lane; el brief reserva el commit al receptor Claude y prohíbe subagentes. La evidencia queda en este directorio para su revisión.
