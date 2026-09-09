# Lane S3b — Ronda 2

**B1, B2 y B4 corregidos y verificados offline. B3 implementado y probado en su mecanismo; NO cerrado por su gate: la suite completa sigue roja en este sandbox.** El receptor debe ejecutar el mismo gate en su entorno antes de aceptar el cierre. No se atribuyen los timeouts al coste sin haber aislado esa causa.

Destinatario: Claude (Opus 5). Fecha: 2026-09-09. Árbol utilizado: `C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev`; la shell no puede abrir `P:`. El veredicto de Grok se leyó antes de editar. Esta entrega modifica únicamente los cinco ficheros asignados en `tools/` y esta carpeta.

Código de partida conservado en [ronda2-before/](ronda2-before/). Diff de esta ronda generado con `difflib`, sin git: [ronda2-changes.patch](ronda2-changes.patch). Las referencias de código siguientes corresponden al contenido probado en esta ronda.

## 1. B1 — Censo del runner y demás cargas por fichero

**Cambio.** `tools/dayz_mcp/server_freshness.py:27` incluye el nombre `dayz_playbook_runner`, incluso con alias de unidad, y módulos cuyo fichero está en la carpeta de `playbooks/`. El cargador real sigue siendo `tools/dayz_mcp/playbook_tool.py:61`: registra el módulo en `sys.modules` en `:73`, ejecuta el fichero en `:75` y conserva `_runner` en `:78`. Se invoca ese cargador durante el import del servidor, antes de la referencia inicial (`tools/dayz_mcp/server.py:63`, `:65`). No se vacía su caché ni se recarga código.

**Censo realizado, no lista asumida.** Búsqueda ejecutada sobre los Python de `tools/` y `playbooks/`:

```powershell
rg -n -g '*.py' -g '!**/tests/**' -g '!**/native-launchers/**' 'spec_from_file_location|SourceFileLoader|exec_module|import_module\(' tools playbooks
```

Único cargador encontrado en ese código de producción: `tools/dayz_mcp/playbook_tool.py:69` / `:75`. El barrido adicional incluyendo tests encontró cargadores de fixtures en `test_daemon`, `test_dayz_test_app`, `test_launcher_registry_update`, `test_docs_truth`, `test_night0909_entity_wait`, `test_process_job_spike`, `test_security_runtime_audit`, `test_server_freshness`, `test_steamfastpath_repair` y `test_task9_launcher_migration`; no son el proceso de tools de producción. Los bundles de `native-launchers/` se excluyeron y su contenido no se declara inspeccionado.

**Prueba.** `tools/tests/test_server_freshness.py:391` usa el cargador y la caché reales sobre un runner temporal. Llama al `playbook_run` registrado, edita `_async_run` en disco y repite dos llamadas: se mantiene `implementation=old`, `isError=false` y la marca nombra `dayz_playbook_runner` como `stale`. Las pruebas de `:415` y `:420` cubren alias de unidad y otro nombre de módulo bajo `playbooks/`; si ese segundo módulo llega tarde, hay `loaded_after_server_snapshot`, nunca una nueva referencia silenciosa.

## 2. B2 — Dependencias de producción cargadas antes de la referencia

**Cambio.** Import explícito de `native_bundle` y `native_launcher_backend` en Windows en `tools/dayz_mcp/server.py:61-62`; `registry_lock` en `:35`; runner en `:63`; referencia congelada en `:65`. Se cargan definiciones, sin invocar launchers ni adquirir locks.

El seguimiento de imports reales encontró, además del par señalado por Grok, cinco incorporaciones al conjunto anterior: `native_bundle`, `native_launcher_backend`, `native_child_announcement`, `native_debug_state` y `dayz_tools_paths`. `request_path_authority` ya estaba cargado. La única arista local restante sin cargar era `launcher_registry.py:295` → `registry_lock`; también se incorporó. El punto de import tardío original sigue visible en `secure_launcher.py:116` y `:234`.

**Prueba.** `tools/tests/test_server_freshness.py:428` inicia un intérprete Python independiente, importa el servidor antes que otros tests y verifica tanto el conjunto esperado como las aristas de import `dayz_mcp` de las fuentes cargadas. No permite que unittest discovery caliente previamente las dependencias. Comprueba `missing_imports=[]` y `fresh` antes/después de usar la caché del runner. Resultado independiente conservado en [ronda2-fresh-process.json](ronda2-fresh-process.json): **58 fuentes, `stale=[]`, `unreadable=[]`, `status=fresh`**.

La referencia sigue siendo del proceso, no de cada `build_app`. La regresión `test_rebuilding_app_does_not_reanchor_loaded_code` sigue pasando. Los imports futuros sin referencia inicial continúan siendo `unknown`; esta ronda elimina los imports tardíos del camino conocido de producción, no inventa una atestación para módulos desconocidos.

## 3. B3 — `stat` primero; hash cuando cambia

**Cambio.** `tools/dayz_mcp/server_freshness.py:51` obtiene `(st_mtime_ns, st_size, st_ino)`. `ServerSourceWatch` (`:61`) conserva el hash inicial inmutable y el último hash/identidad observado. En `:102-118`, un `stat` idéntico reutiliza el hash; una identidad distinta provoca una lectura y comparación. También se cachea el hash diferente: seguir `stale` no provoca otra lectura por llamada. Un cambio de metadatos durante la lectura impide confirmar esa lectura. Un lock en `:84` serializa las actualizaciones entre observaciones concurrentes ejecutadas con `to_thread`.

No se añade un sondeo periódico ni se cambia el daemon. Su precedente leído es `tools/dayz_mcp/daemon.py:650-674`. Se conservan las observaciones de entrada y salida y la lectura propia de `bridge_status`, con hashes evitados cuando el triple no se mueve.

**Pérdida aceptada y explícita.** Una edición que preserve **tamaño, mtime e identidad de fichero** resulta invisible mientras ese triple no cambie. También puede pasar inadvertida una denegación de lectura por ACL si `stat` conserva el mismo triple. Se documenta en el código (`server_freshness.py:64-66`) y en la descripción pública (`server.py:3274-3275`). No se afirma haber conservado la detección adversarial de la ronda 1.

**Pruebas de coste y de contenido.** `tools/tests/test_server_freshness.py:352` registra cero `Path.read_bytes` en una llamada normal y un `bridge_status` sin cambios. `:358` verifica una única lectura del fichero editado entre dos llamadas y un status, todos todavía `stale`. `:367` verifica una única lectura tras un touch con bytes iguales; después sigue `fresh`. `:375` prueba que reemplazar el fichero con la misma mtime/tamaño sí se detecta por `st_ino`. Los tests de `:178` y `:387` fijan las dos pérdidas aceptadas. El de `:201` exige que una lectura denegada tras cambio de `stat` produzca `unknown` y se recupere cuando vuelve a ser legible.

**Gate completo: NO verde.** Ejecución literal, sin cambiar el runner ni el intérprete:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run-tests.ps1
```

Salida literal de [ronda2-full-suite.log](ronda2-full-suite.log):

```text
Ran 3291 tests in 264.584s

FAILED (failures=3, errors=78, skipped=28)
Exit code: 1
```

Los tres casos indicados por el receptor salieron **`ok` dentro de esta corrida completa**:

- `tests.test_loopback.LoopbackTest.test_h0_server_primitives_route_and_validate_args` — log `:3527-3528`.
- `tests.test_session_e2e.SessionE2ETest.test_parallel_reads_and_mutations_are_fifo_non_interleaved` — log `:6055-6056`.
- `tests.test_daemon_spawn_branch.DaemonEventRecordTest.test_a_held_writer_lock_does_not_hold_up_the_caller` — log `:1435-1437`.

Los dos timeouts **no se reprodujeron aquí después del cambio**. Esto no demuestra que el coste fuera su causa; no hubo un experimento que lo aislase en el entorno del receptor. No se aumentaron timeouts ni se añadieron skips. B3 continúa pendiente de su criterio explícito: suite completa verde en ese entorno.

Los 81 encabezados de fallo/error, incluidos subtests, coinciden con los del log final de ronda 1: cero casos añadidos o eliminados. Comparación conservada en [ronda2-failure-comparison.json](ronda2-failure-comparison.json), no inferida solo del recuento. Ningún fallo/error pertenece a los tres módulos de tests afectados por esta entrega. Los tres FAIL siguen siendo:

- `test_bug046_startup_deadlock.DaemonStartupElectionProcessTest.test_run_daemon_candidate_wave_and_cross_port_publish_one_generation`: acceso denegado al inspeccionar procesos; `process_scan_incomplete`.
- `test_dayz_tools_paths.DayZToolsPathsTest.test_require_names_every_tried_tools_root_when_none_exist`: diferencia `C:` / `c:` en la cadena de ruta esperada.
- `test_lifecycle_reconcile.LifecycleReconcileTest.test_both_request_parser_hashes_are_printed_in_the_same_case`: manifest del launcher ilegible; no obtiene ambos hashes.

Los ERROR incluyen acceso denegado a bundles/procesos, `native_launcher_create_failed` e `invalid_dayz_test_path_authority`. [ronda2-suite-failures.txt](ronda2-suite-failures.txt) conserva todos los tracebacks. La coincidencia con la ronda anterior no prueba una causa única ni permite declarar el gate aprobado. No se intentó reparar esas superficies.

## 4. B4 — Canario de identidad del enganche y tipo de resultado

**Cambio.** Cada observación de producción usa `observe()` (`tools/dayz_mcp/server_freshness.py:193`), que vuelve a consultar `app._mcp_server.request_handlers`. Comprueba tanto el objeto servidor como **`live_handlers.get(CallToolRequest) is handler`** (`:199-202`); no consulta solo el dict capturado al instalar. Una discrepancia produce `server_modules.status=unknown`, `observation_errors.call_tool_hook` con su razón y el booleano conservador `true`. Una diferencia de fuentes simultánea se conserva en `stale`, pero no convierte un canario roto en una observación válida.

`bridge_status` llama a este observador por su propio closure (`tools/dayz_mcp/server.py:3347-3349`), independientemente de que el wrapper siga en el despacho. Por eso, si el SDK vuelve a registrar su handler original y lo sustituye, el payload ordinario visible de `bridge_status` muestra `unknown` y su razón aunque esa llamada haya esquivado el wrapper. Instalación y observador por app en `server.py:5344`; la referencia de fuentes puede compartirse entre apps sin confundir sus canarios.

Si `response.root` deja de ser `CallToolResult`, ya no se omite la marca (`server_freshness.py:211-223`): se devuelve un `CallToolResult` de error explícito con marca `unknown` y razón `unexpected_call_tool_result_type`. Ese fallo de compatibilidad queda registrado para las observaciones posteriores. No se pretende preservar como resultado de dominio un envelope incompatible. Los resultados normales conservan contenido, estructura, esquema, metadatos previos e `isError`.

**API contrastada.** El SDK instalado registra el handler en `tools/.venv-mcp/Lib/site-packages/mcp/server/fastmcp/server.py:308`; el lowlevel lo guarda en `mcp/server/lowlevel/server.py:586` y despacha desde el diccionario vivo en `:729`. Se mantiene el pin `mcp==1.27.2` de `tools/requirements-mcp.txt:1`.

**Pruebas.** `tools/tests/test_server_freshness.py:472` usa una sesión MCP real en memoria, re-registra el handler del SDK y recibe `unknown` en el JSON ordinario. `:487` conserva el dict fósil con el wrapper y sustituye el dict vivo: tampoco informa fresh. `:501` usa un callable cuya igualdad devuelve true para probar que se exige identidad. `:517` quita el enganche durante una llamada; `:524` sustituye el servidor lowlevel; `:535` suministra un tipo de resultado incompatible; `:550` verifica canarios independientes para dos apps. Todos PASS.

## 5. Puntos no bloqueantes y discrepancias con la revisión

**Contrato booleano cerrado.** `source_stale()` devuelve siempre `bool` (`tools/dayz_mcp/server_freshness.py:141`). `false` significa exclusivamente fuente/observación verificablemente fresh; `true` significa stale **o** unknown. La distinción y las razones están en `server_modules.status`, `unreadable_reasons` y `observation_errors`. Descripción pública en `server.py:3270-3273`. `test_mcp_tools.py:656` ya no acepta bool/dict alternativamente; `:670` prueba con `assertIs` las tres situaciones y las palabras del contrato público.

**Segundo bloque visible.** Se conserva como texto normal `SERVER_CODE_FRESHNESS …`, no como otro objeto JSON (`server_freshness.py:167`). `_meta` mantiene los datos para consumo estructurado. Se eligió esto para que un modelo que no reciba `_meta` vea estado, módulos, razones y remedio. `content[0]` y `structuredContent` siguen siendo el payload original; el descriptor advierte que los bloques se leen por separado. Un consumidor que concatene todos los textos y exija un único JSON sigue siendo incompatible con una respuesta que añade diagnóstico; no se afirma haber corregido ese consumidor.

Ensayo wire conservado en [ronda2-observable.json](ronda2-observable.json): ejecutor temporal sigue devolviendo `old`, el booleano pasa de false a true, la marca es stale y `isError=false`. Las pruebas de errores UI conservan la comparación exacta del texto de dominio y comprueban separadamente el aviso (`tools/tests/test_ui_error_diagnostics.py:163-168`).

**Cuatro citas corregidas.** [INFORME.md](INFORME.md) queda rotulado como histórico de R1 y conserva sus pruebas, decisiones y resultados de entonces. Se corrigieron las cuatro erratas contra las copias R1: publicación `server_modules` **3337**; fingerprint/hora **682-683**; referencia de fuentes **3331 / 60**; registro FastMCP **308**. Sus anclas equivalentes en el código R2 son **server.py:3348**, **:687-688**, **:3342 / :65**, y **fastmcp/server.py:308**.

**No se rechaza ninguno de B1–B4.** Sí se acota una afirmación secundaria de `GROK-VERDICT.md:40`: un `OSError` de `read_bytes` no tumbaba por sí mismo la tool, pues `_digest` ya lo capturaba en la copia R1 `ronda2-before/server_freshness.py:42-48`; sigue capturado en `tools/dayz_mcp/server_freshness.py:42-48`. Las pruebas de denegación confirman `unknown` sin convertir la llamada normal en fallo. Esto no invalida B3 ni demuestra nada sobre los timeouts del receptor.

T2 conserva su NO: no se añade hot reload, reinicio ni automatización de reciclado del proceso.

## 6. Tests nuevos y validación final

Nuevos en R2, una entrada por test; todos PASS en el focal y sin fallo en la suite completa:

- `tests.test_server_freshness.ServerFreshnessTest.test_unchanged_calls_do_not_read_source_bytes` — `tools/tests/test_server_freshness.py:352`.
- `tests.test_server_freshness.ServerFreshnessTest.test_changed_source_is_hashed_once_then_stale_is_cached` — `tools/tests/test_server_freshness.py:358`.
- `tests.test_server_freshness.ServerFreshnessTest.test_identical_touch_is_hashed_once_then_fresh_is_cached` — `tools/tests/test_server_freshness.py:367`.
- `tests.test_server_freshness.ServerFreshnessTest.test_file_id_change_detects_same_size_and_mtime_replacement` — `tools/tests/test_server_freshness.py:375`.
- `tests.test_server_freshness.ServerFreshnessTest.test_same_stat_read_deny_is_the_documented_cache_miss` — `tools/tests/test_server_freshness.py:387`.
- `tests.test_server_freshness.ServerFreshnessTest.test_cached_playbook_runner_stays_old_and_is_marked_on_wire` — `tools/tests/test_server_freshness.py:391`.
- `tests.test_server_freshness.ServerFreshnessTest.test_named_runner_is_watched_through_a_drive_alias` — `tools/tests/test_server_freshness.py:415`.
- `tests.test_server_freshness.ServerFreshnessTest.test_other_playbooks_module_is_censused_with_unknown_for_late_import` — `tools/tests/test_server_freshness.py:420`.
- `tests.test_server_freshness.ServerFreshnessTest.test_fresh_process_preloads_production_import_closure` — `tools/tests/test_server_freshness.py:428`.
- `tests.test_server_freshness.ServerFreshnessTest.test_detached_hook_is_unknown_in_real_bridge_status_payload` — `tools/tests/test_server_freshness.py:472`.
- `tests.test_server_freshness.ServerFreshnessTest.test_fossil_handler_dictionary_cannot_report_fresh` — `tools/tests/test_server_freshness.py:487`.
- `tests.test_server_freshness.ServerFreshnessTest.test_equal_but_different_live_handler_is_unknown` — `tools/tests/test_server_freshness.py:501`.
- `tests.test_server_freshness.ServerFreshnessTest.test_hook_detached_during_call_is_unknown_at_exit` — `tools/tests/test_server_freshness.py:517`.
- `tests.test_server_freshness.ServerFreshnessTest.test_replaced_lowlevel_server_is_unknown` — `tools/tests/test_server_freshness.py:524`.
- `tests.test_server_freshness.ServerFreshnessTest.test_unexpected_result_type_is_visible_unknown_not_omitted` — `tools/tests/test_server_freshness.py:535`.
- `tests.test_server_freshness.ServerFreshnessTest.test_apps_sharing_source_baseline_have_independent_canaries` — `tools/tests/test_server_freshness.py:550`.
- `tests.test_mcp_tools.MCPToolsTest.test_bridge_status_source_stale_is_boolean_for_all_three_states` — `tools/tests/test_mcp_tools.py:670`.

Además se adaptaron `test_same_stat_edit_is_the_documented_cache_miss` y `test_read_denied_after_stat_change_is_unknown_and_recovers`, que sustituyen las expectativas anteriores sobre hash incondicional; los tests de unknown ahora exigen bool conservador y estado separado.

Recuentos literales focales:

```text
Ran 39 tests in 6.927s

OK
Tests run: 39
Exit code: 0

Ran 52 tests in 8.503s

OK
Tests run: 52
Exit code: 0

Ran 10 tests in 0.647s

OK
Tests run: 10
Exit code: 0
```

Logs: [frescura](ronda2-freshness.log), [tools](ronda2-mcp-tools.log), [UI](ronda2-ui.log). Son **101 pruebas focales**. El recuento completo literal se conserva en la sección B3 y [ronda2-suite-summary.txt](ronda2-suite-summary.txt): **3291**, con **3 failures, 78 errors, 28 skipped**, exit **1**.

La sintaxis se comprobó con `compile` antes de escribir los cinco Python. Los SHA-256 previos al gate se volvieron a contrastar después: [ronda2-tested-source-hashes.json](ronda2-tested-source-hashes.json). Ninguna fuente probada cambió durante el gate. El runner conserva SHA-256 `2b08dea99f89c709c645c65610cba781c356f3f0d1a0f48976a49b8333245908`. Los hashes observados de `inbox.py` y `test_pipeline_feedback.py` tampoco cambiaron durante esa ventana; no se editaron.

## 7. LO QUE NO PUDE VERIFICAR

- **Cierre de B3 en el entorno del receptor.** La suite de este sandbox es roja; la política de aprobación `never` impide ejecutarla con elevación. Los fallos de acceso se documentan; no se eluden ni se corrigen fuera de alcance.
- **Causa de los dos timeouts previos.** Aquí salieron ok dentro de la suite completa. No existe un experimento antes/después en el mismo entorno del receptor que aísle el coste.
- **Un `dayz_test_run` de producción nuevo.** B2 se acredita con imports de producción, un intérprete limpio y pruebas con ejecutor inocuo; no se lanzó DayZ, tocó Steam ni el registro.
- **Activación en el proceso MCP ya abierto.** Los cambios en disco no le instalan retrospectivamente el observador. No se mató ni reinició el daemon, el servidor, el cliente ni la sesión MCP existente. La suite autorizada usa sus propios fixtures de procesos.
- **Estado final del run 42601b07 y de leases/cola.** Se intentó únicamente `session_status`; la herramienta devolvió `MCP tool call requires approval, but approval policy is never`. No hubo estado legible ni un cierre de producción que se pueda certificar. No se adquirió lease porque esta lane no realizó una secuencia mutante sobre el juego.
- **Latencia máxima en OneDrive bajo bloqueo/hidratación.** La prueba mecánica demuestra eliminación de lecturas repetidas de contenido; los propios `stat` y la primera lectura de un cambio aún dependen del almacenamiento.
- **Ediciones que preserven el triple, ACL del mismo objeto y modificaciones transitorias entre observaciones.** Son pérdidas y límites explícitos. Tampoco hay atestación de bytecode, monkeypatches ni de la ventana entre import y captura inicial.
- **Cambios futuros de arquitectura del SDK que esquiven toda la observación.** El canario acredita la superficie leída de 1.27.2. Si el wrapper está completamente fuera del despacho, el aviso independiente se observa en `bridge_status`; no se promete que `_meta` se añada a otras tools que lo esquiven. No se ha probado otro SDK ni el renderizado del host Claude/Codex en una sesión de producción.

## 8. Entrega al receptor

Sin git: no commit, add, branch, stash ni consulta git. No se tocaron los cambios ajenos en `tools/dayz_mcp/inbox.py` ni `tools/tests/test_pipeline_feedback.py`. Tampoco se escribió fuera de `tools/` y esta carpeta.

Este informe es el handoff autorizado. No se actualizó memoria, vault, buzones ni el HANDOFF compartido: la petición restringe expresamente las escrituras. Se aplicó el cierre de `post-session` dentro de ese límite; el receptor puede enrutar los límites de entorno desde este informe.

Siguiente acción del receptor: leer [ronda2-changes.patch](ronda2-changes.patch), ejecutar el gate completo indicado en su entorno y decidir el cierre de B3 y el commit. No hay una suite verde declarada por esta lane.
