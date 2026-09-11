# Lane S3 — fb-20260909-135758-f5f4

> Informe historico de la ronda 1. Las anclas de los cinco ficheros modificados se refieren a las copias R1 conservadas en [ronda2-before](ronda2-before/). Las cuatro erratas de citas de Grok estan corregidas aqui; el codigo vigente, contrato y gate de ronda 2 estan en [RONDA2.md](RONDA2.md).

**T1 implementado y verificado offline. T2: NO. El ticket no queda cerrado: la suite completa se ejecutó, pero sigue roja en este entorno.** Entrega para Claude; revisión de Grok pendiente. No se revisó ni corrigió trabajo ajeno.

Árbol utilizado: `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`. La shell no tiene unidad `P:`. Código y tests en `tools/`; evidencias y este informe en la carpeta de entrega. Sin comandos git, commit, cambios de intérprete ni cambios en `tools/run-tests.ps1`.

## 1. T1 — Observable entregado

| Superficie | Contrato y ancla |
|---|---|
| Referencia del proceso | `tools/dayz_mcp/server.py:60` captura las fuentes durante el import de server, antes de construir apps. Reconstruir `build_app` no renueva esa referencia. `knowledge` se importa antes para incluir sus tools. |
| Cobertura | `tools/dayz_mcp/server_freshness.py:26`: módulos cargados `dayz_mcp.*`, el paquete y helpers locales de `tools/`. Incluye explícitamente `mcp_capture` y `knowledge_pack` incluso con alias de unidad. Las listas usan nombres Python completos, evitando colisiones entre basenames. |
| Lectura y comparación | `tools/dayz_mcp/server_freshness.py:42` y `:58`: SHA-256 del contenido frente a la referencia inicial. Un mero touch no es stale; cambiar bytes conservando tamaño y mtime sí lo es. Una fuente borrada, denegada o sin referencia comprobable se publica como unreadable, con motivo. |
| `bridge_status.server_modules` | `tools/dayz_mcp/server.py:3337`: `server_started_at`, `server_pid`, `watched_count`, `stale`, `unreadable`, `unreadable_reasons`. `server_started_at` es la hora de la instantánea inicial, no una consulta al nacimiento del proceso del SO. El bloque del daemon se conserva independiente. |
| `tool_registry_source_stale` | `tools/dayz_mcp/server_freshness.py:90`: `true` si hay alguna diferencia; `false` si hay cobertura y todas las fuentes son comprobables e iguales; en otro caso, objeto `{"status":"unknown","reason":"unverifiable_server_sources","modules":{...}}`. El motivo queda en el propio campo. Una diferencia probada prevalece aunque otra fuente sea ilegible. |
| Resultado de cualquier tool MCP | `tools/dayz_mcp/server_freshness.py:122`, instalado en `tools/dayz_mcp/server.py:5333`: inspección antes y después de la llamada; unión de ambas observaciones. Añade `_meta.server_code_freshness` y un bloque de texto JSON visible con el mismo campo. |
| Descripción pública | `tools/dayz_mcp/server.py:3263` documenta fuentes, resultado y remedio. Fingerprint y captured_at del registro siguen congelados en `server.py:682-683`; ya no se comparan permanentemente con una autoridad vacía. |

La marca significa **“este proceso conserva fuentes cambiadas o no comprobables”**, con `scope="loaded_server_modules"`. No atribuye la ejecución a una dependencia concreta que no se haya trazado. Es deliberadamente conservadora: evita mantener una tabla manual de dependencias por tool que pueda omitir, por ejemplo, un validador llamado desde `dayz_test_run`.

Se conserva `structuredContent`, cada bloque original, el esquema de salida, los metadatos previos y `isError`. La frescura no convierte una llamada correcta en error ni oculta un error real. El campo del protocolo permite consumo automático; el bloque de texto evita depender de que el cliente enseñe `_meta` al modelo. Las llamadas internas directas a `app.call_tool` no son respuestas de protocolo: reciben la marca al salir la llamada MCP exterior.

El enganche envuelve el manejador **después** de la normalización/validación del SDK. APIs leídas en la instalación fijada por `tools/requirements-mcp.txt:1` (`mcp==1.27.2`): `tools/.venv-mcp/Lib/site-packages/mcp/server/fastmcp/server.py:308` registra el manejador; `mcp/server/lowlevel/server.py:559` valida la salida, `:572` crea `CallToolResult`, `:583` convierte errores y `:586` registra `CallToolRequest`. El uso de `app._mcp_server` depende de esa integración y está cubierto por una sesión MCP real en memoria, no sólo por llamadas a la función auxiliar.

**Cómo se ve el caso de hoy.** [observable.json](observable.json) contiene una ejecución medida: se importa un ejecutor inocuo desde un fichero temporal, se construye la app real, se cambia el fichero de `old` a `new` y se llama al `dayz_test_run` registrado. No se reimporta el ejecutor:

```json
{
  "structuredContent": {"status": "succeeded", "implementation": "old"},
  "isError": false,
  "_meta": {
    "server_code_freshness": {
      "status": "stale",
      "scope": "loaded_server_modules",
      "stale": ["dayz_mcp.dayz_test_tool"],
      "unreadable": [],
      "remediation": "reopen_mcp_client"
    }
  }
}
```

Además de esos campos, `content` lleva el JSON original y un segundo texto con `{"server_code_freshness": {...}}`. El artefacto completo incluye PID/hora. En el mismo proceso, `bridge_status.tool_registry_source_stale` pasa de `false` a `true`.

El arranque independiente sin construir app ni contactar al daemon observó **51 módulos, stale=[], unreadable=[], source_stale=false**: [fresh-process.json](fresh-process.json). Una lectura tardó 6,30 ms en ese ensayo; no es una cota de latencia. Se hacen dos lecturas por llamada MCP, más la lectura propia de bridge_status, fuera del event loop mediante `asyncio.to_thread`. Se eligió releer bytes para no conservar los falsos negativos de una cache basada sólo en metadatos.

## 2. T2 — NO

No se añade `daemon_reload(scope="tools")`, recarga de hojas ni autorreciclado. Fallos concretos de este árbol:

- **Registro y closures antiguos.** `tools/dayz_mcp/server.py:3276` crea el runtime que capturan las tools. `:3320` comprueba su clase mediante `isinstance`; una recarga del módulo podría dejar una instancia de la clase anterior frente a la clase nueva. `:3331` retiene la referencia de fuentes por app, capturada en `server.py:60`. Reimportar el módulo no reconstruye las funciones ya registradas.
- **Validadores encadenados.** `tools/dayz_mcp/server.py:1930` y `:1950` capturan métodos previos de `fn_metadata`; `:1938` y `:1958` los sustituyen en objetos ya construidos. Reimportar funciones sueltas no actualiza esos closures ni demuestra que el schema publicado y el validador vivo sigan juntos.
- **Estado de sesión que sí vive en el servidor de tools.** `tools/dayz_mcp/server.py:1116` tiene el lock; `:1205` crea identidad con un UUID nuevo y `:1218` crea el ControlClient. `tools/dayz_mcp/control_client.py:161` conserva lease, ticket, operation ID y locks. `server.py:3568` puede tener una tarea de heartbeat de box en vuelo. No hay aquí un traspaso probado de ese estado hacia un proceso sucesor.
- **Otros registros tampoco son hojas puras.** `tools/dayz_mcp/knowledge.py:620` fija un path que captura `read_installed_index` en `:622`. Reimportar knowledge no sustituye los callbacks de la app.
- **El transporte pertenece al proceso que se quiere reciclar.** `tools/dayz_mcp/server.py:5411` sirve stdio. No se implementó ni probó un supervisor que drene llamadas, confirme su respuesta y vuelva a conectar el cliente anfitrión a otro proceso. En embedded, además, `server.py:910` posee loopback y `:5413` lo cierra al salir: no puede generalizarse un reciclado seguro entre modos.

**Los runs sí están en el daemon**, según el mecanismo leído: `tools/dayz_mcp/daemon.py:413` crea RunManifestStore; `:520` construye ProcessLifecycle con ese manifest; `:598` lo guarda en el estado; `:730` publica lifecycle en status. La independencia respecto al padre se documenta y aplica en `tools/dayz_mcp/server.py:5392`.

Eso **no demuestra conservación incondicional de la partida al reciclar el servidor de tools**. La expiración de lease entra por `tools/dayz_mcp/session_coordination.py:2084`; el cleanup está conectado en `daemon.py:451` y `:467`, y `daemon.py:90` llama a `begin_release_owner`. Este último distingue lanzamientos aún no confirmados en `process_lifecycle.py:3358`; su limpieza puede llegar a `guard.terminate` en `:3423`. No afirmo que cerrar un cliente MCP termine todo run; afirmo que el propietario del manifest no basta para probar un reciclado seguro durante cualquier fase.

Queda sin cubrir **aplicar el código nuevo desde la propia sesión**. El remedio sigue siendo abrir externamente un proceso servidor MCP nuevo con las fuentes nuevas. La decisión NO cierra T2, no promete solucionar ese límite operativo.

## 3. Tests y gate

Tests nuevos en `tools/tests/test_server_freshness.py`, todos PASS (una entrada por test):

- `tests.test_server_freshness.ServerFreshnessTest.test_fresh_response_has_no_marker` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_dayz_run_keeps_old_behavior_and_marks_stale_on_wire` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_bridge_status_is_live_but_registry_fingerprint_is_frozen` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_rebuilding_app_does_not_reanchor_loaded_code` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_identical_content_touch_is_fresh` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_same_size_and_mtime_edit_is_stale` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_deleted_source_is_unknown_and_call_still_succeeds` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_read_denied_is_unknown_without_failing_tool` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_unreadable_initial_snapshot_never_becomes_false_fresh` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_late_import_has_explicit_unknown_reason` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_changed_module_origin_is_unknown` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_stale_at_entry_is_retained_if_source_is_restored_in_call` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_source_edit_during_call_is_marked` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_stale_witness_wins_over_another_unreadable_source` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_error_flag_and_original_error_text_are_preserved` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_closed_argument_validation_is_preserved_and_marked` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_image_and_existing_content_are_preserved` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_list_result_and_output_schema_are_preserved` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_existing_result_metadata_is_preserved` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_loaded_watch_covers_registered_implementations_and_helpers` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_empty_watch_is_unknown` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_named_capture_helper_is_watched_through_a_drive_alias` - PASS.
- `tests.test_server_freshness.ServerFreshnessTest.test_stale_marker_survives_a_real_mcp_client_session` - PASS.

Ensayo principal de código antiguo: `tools/tests/test_server_freshness.py:125`. Cliente MCP real en memoria: `:323`. Los fixtures usan un ejecutor temporal inocuo y un runtime de pruebas; no contactan al daemon, Steam ni DayZ.

Regresiones adaptadas:

- `tools/tests/test_mcp_tools.py:656`: nuevo contrato bool/objeto unknown, manteniendo la prueba de fingerprint/captured_at congelados.
- `tools/tests/test_ui_error_diagnostics.py:163`: comprueba separadamente la marca y conserva la igualdad exacta del texto original de error. **10 tests PASS**: [ui-regression.log](ui-regression.log).

Gate focal literal, [focused-tests.log](focused-tests.log):

```text
Ran 23 tests in 3.058s

OK
Tests run: 23
Exit code: 0
```

Gate completo ejecutado, sin alterar runner ni intérprete:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run-tests.ps1
```

**Resultado final literal**, [full-suite-final.log](full-suite-final.log):

```text
Ran 3274 tests in 238.573s

FAILED (failures=3, errors=78, skipped=28)
Exit code: 1
```

Por tanto, **NO se cumple el requisito de suite entera verde**. La última corrida no tiene fallos en los tres módulos de tests afectados por S3. [suite-failures-final.txt](suite-failures-final.txt) conserva todos los tracebacks.

Los tres FAIL pendientes son:

1. `tests.test_bug046_startup_deadlock.DaemonStartupElectionProcessTest.test_run_daemon_candidate_wave_and_cross_port_publish_one_generation`: `psutil.AccessDenied / WinError 5` al leer un proceso Python; termina en `process_scan_incomplete`.
2. `tests.test_dayz_tools_paths.DayZToolsPathsTest.test_require_names_every_tried_tools_root_when_none_exist`: espera `C:\Program Files (x86)`, recibe `c:\program files (x86)`. Reproducido aislado: **13 tests, 1 fallo**, [isolated-tools-paths.log](isolated-tools-paths.log). Este módulo importa el resolutor de rutas, no el servidor nuevo.
3. `tests.test_lifecycle_reconcile.LifecycleReconcileTest.test_both_request_parser_hashes_are_printed_in_the_same_case`: manifest del launcher ilegible; sólo obtiene uno de los dos hashes.

Los ERROR incluyen acceso denegado a ficheros de `tools/native-launchers/dayz-test-v1`, tres `process_scan_incomplete`, errores de creación del launcher y `invalid_dayz_test_path_authority`. Este último se reproduce también en el módulo aislado de políticas: **30 tests, 1 error, 1 omitido**, [isolated-launcher-policy.log](isolated-launcher-policy.log). No se atribuye una causa raíz común no demostrada a los 78 errores; no se alteraron los módulos implicados ni las restricciones para ocultarlos.

La carrera avisada de `test_a_held_writer_lock_does_not_hold_up_the_caller` salió **ok**; no fue uno de los fallos.

Primera corrida conservada en [full-suite.log](full-suite.log): `Ran 3272 tests in 247.522s`, `FAILED (failures=6, errors=78, skipped=28)`. Sus tres fallos adicionales eran las expectativas de texto de UI adaptadas arriba. No se presentan como ajenos.

Sintaxis de los cinco ficheros Python verificada con `compile`, sin generar bytecode. Hashes finales contrastados tras la suite: [tested-source-hashes.json](tested-source-hashes.json). El runner mantiene SHA-256 `2B08DEA99F89C709C645C65610CBA781C356F3F0D1A0F48976A49B8333245908`.

## 4. LO QUE NO PUDE VERIFICAR

- **La suite global en verde.** Faltan la ejecución del receptor con acceso al entorno operativo y la resolución/atribución de sus fallos restantes. No se rebajó el gate, filtraron tests ni añadieron skips.
- **Activación en la sesión MCP que ya estaba abierta.** Ese proceso todavía ejecuta sus imports anteriores. Este parche en disco no le instala retrospectivamente el observador.
- **El run 42601b07 y el estado final de leases/cola.** Intenté `session_status`; el sistema respondió literalmente: `MCP tool call requires approval, but approval policy is never`. No hubo resultado de estado ni intento de sortear el bloqueo.
- **Renderizado de la marca por Claude/Opus o Codex en una sesión de producción nueva.** Se verificaron el envelope serializado y un cliente/servidor MCP reales en memoria. No se verificó esa interfaz de usuario ni se repitió el lanzamiento DayZ de hoy.
- **Reciclado seguro y conservación de juego bajo llamadas en vuelo.** Sólo está argumentado el NO a partir del mecanismo leído; no se ejecutó un reinicio de producción.
- **Atestación del bytecode o de la ventana anterior a la instantánea.** Se comparan fuentes desde el snapshot inicial durante el import de server, como referencia operativa. Una edición entre el import previo de una dependencia y esa captura puede quedar fuera; no se inspeccionan pyc antiguos, monkeypatches, extensiones nativas ni dependencias de terceros. Un módulo tardío sin baseline se marca unknown, incluso si acaba de importar bytes actuales.
- **Captura de todo cambio transitorio dentro de una llamada.** Hay sondeos de entrada/salida, no un monitor continuo. Un cambio que aparezca y se revierta enteramente entre ambos puede no observarse. Restaurar las fuentes exactas de la referencia permite volver a fresh; cambiar sólo el reloj no.
- **Coste bajo carga sostenida o almacenamiento bloqueado.** Hay una medición local de lectura, no una garantía de latencia.

No se gestionaron los procesos de producción, no se lanzaron partidas, ni se tocaron Steam o el registro. La suite autorizada sí crea y limpia sus propios fixtures de procesos. No se adquirió lease porque esta lane no realizó una secuencia mutante sobre el juego.

## 5. COMO LO COMPRUEBO YO

**[EXACT] Verificación offline que puedes ejecutar desde la sesión actual**, mediante shell, sin reiniciar tu propio servidor MCP:

```powershell
Set-Location -LiteralPath 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
& .\tools\.venv-mcp\Scripts\python.exe -B .\reviews\mcpreload-2026-09-09\verify_s3.py
powershell -ExecutionPolicy Bypass -File tools\run-tests.ps1 -Module test_server_freshness
powershell -ExecutionPolicy Bypass -File tools\run-tests.ps1
```

El primer comando **crea un proceso Python nuevo e independiente** y una app MCP de prueba dentro de él. Carga un ejecutor inocuo desde un temporal y edita sólo ese temporal. No ejecuta `python -m dayz_mcp --client`, no sirve stdio a tu anfitrión, no abre el puerto del daemon y no lanza DayZ. Reproduce el observable a través del manejador MCP registrado; no acredita actualización del proceso de tu sesión.

Salida que decide:

- `verify_s3.py`: exit 0 y `"verdict":"PASS"`; `fresh_source_stale=false`; `stale_source_stale=true`; `stale_tool_result.structuredContent.implementation="old"`; `isError=false`; la marca `status="stale"` nombra `dayz_mcp.dayz_test_tool` tanto en `_meta` como en el último bloque de texto. Una aserción fallida termina con exit distinto de 0.
- Gate focal: `Tests run: 23`, `OK`, `Exit code: 0`.
- Gate global: **todos los tests**, recuento no vacío, `OK` y `Exit code: 0`. El resultado entregado aquí es rojo y necesita repetirse en el entorno del receptor; no basta el PASS focal.

**[EXACT] Para acreditar activación de producción hace falta un proceso MCP nuevo creado externamente por el cliente anfitrión.** Tu sesión actual no puede recargarse mediante esta entrega. No ejecutes un reinicio del daemon o de DayZ como sustituto.

Una vez disponible esa nueva sesión, `bridge_status` debe mostrar `server_modules`, su PID/hora nuevos y el campo de frescura con el contrato descrito. Antes de otra edición, con todas sus fuentes iguales/comprobables, debe ser `false`. Tras una edición posterior aprobada de un módulo que ya tenía cargado, la siguiente respuesta de cualquier tool MCP debe llevar el bloque visible `server_code_freshness.status="stale"` y nombrarlo. Esto es la comprobación live pendiente, distinta del ensayo offline ya realizado. No es necesario que el bloque independiente `daemon_modules` esté fresco para probar la frescura del servidor de tools.

## Entrega y continuidad

Ficheros modificados/añadidos: `tools/dayz_mcp/server.py`, `tools/dayz_mcp/server_freshness.py`, `tools/tests/test_server_freshness.py`, `tools/tests/test_mcp_tools.py`, `tools/tests/test_ui_error_diagnostics.py`.

[changes.patch](changes.patch) se generó con difflib contra copias anteriores conservadas en esta carpeta, sin consultar ni tocar git. Claude recibe el código para leer el diff, ejecutar su gate y decidir el commit; Grok hará la revisión independiente. No se declara ninguna de esas acciones como realizada.

Este informe es el handoff dentro del alcance autorizado. No se actualizó memoria/vault/HANDOFF compartido ni se escribió en buzones externos: la petición limita las escrituras a `tools/` y esta entrega. Los límites de entorno quedan documentados aquí para que el receptor pueda enrutarlos.
