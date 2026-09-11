# Lane S4 ? Ronda 3

**T1 implementado y verificado offline. T2: NO. Gate completo: NO aprobado.** La entrega a?ade la acci?n acotada de recarga; quedan dos fallos nuevos de documentaci?n cuyos archivos est?n fuera del alcance permitido, adem?s de los errores del gate detallados abajo.

Destinatario: Claude (Opus 5). Fecha: 2026-09-09. Ticket: `fb-20260909-135758-f5f4`. Se leyeron RONDA2, GROK-VERDICT y el m?dulo completo de frescura antes de editar. No se reabre el NO al hot reload del registro FastMCP.

?rbol comprobado desde el host: `C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev`. La shell no pudo abrir `P:` (`CreateProcessWithLogonW failed: 267`). No se us? git. El c?digo previo est? en [ronda3-before/](ronda3-before/); el diff de los ocho archivos de `tools/`, generado con `difflib`, est? en [ronda3-changes.patch](ronda3-changes.patch).

## 1. T1 ? Recarga acotada

**Contrato p?blico [EXACT]:** `playbook_reload(module="dayz_playbook_runner")`.

La tool se registra al construir una app nueva, en `tools/dayz_mcp/server.py:5338`; su firma est? en `:5348`. El par?metro es obligatorio y de tipo `Literal["dayz_playbook_runner"]`. No recibe rutas, listas de m?dulos ni flags para recargar dependencias. Su respuesta de ?xito es:

```json
{"status":"reloaded","module":"dayz_playbook_runner","scope":"current_mcp_process"}
```

Se aplica al singleton del proceso MCP que atiende la llamada. Dos apps dentro de ese proceso comparten runner; otros procesos cliente y el daemon no reciben esta recarga. El wrapper llama al cargador con `asyncio.to_thread` (`server.py:5351`), sin adquirir el lease ni el lock del runtime.

**Carga y publicaci?n.** `tools/dayz_mcp/playbook_tool.py:85` construye un objeto m?dulo nuevo. La entrada can?nica en `sys.modules` existe durante `exec_module` para permitir dataclasses/imports del m?dulo, pero los consumidores del adaptador quedan protegidos por el lock y la admisi?n. Se valida la API usada por el adaptador: `load_playbook`, `_async_run` async y las dos clases de excepci?n. Se comprueba tambi?n que el archivo conserve los bytes le?dos al empezar. Solo entonces se publican el runner y su recibo de carga. Ante una excepci?n, incluso `SystemExit`, se restaura la entrada anterior y se conserva el runner anterior. Una primera carga fallida sin runner previo deja un error tipado y elimina la entrada incompleta.

**Bytes ejecutados.** `_RunnerSourceLoader.get_code` (`playbook_tool.py:79`) compila los bytes capturados, sin consumir ni producir la cach? `.pyc`. API contrastada en `C:/Python314/Lib/importlib/_bootstrap_external.py:753` (`exec_module` llama a `get_code`) y `:818` (`source_to_code` compila el argumento recibido). El runner real define el contrato en `playbooks/runner.py:72`, `:87`, `:91` y `:496`.

**Frescura sin reanclar a mano.** La tool no recibe un `ServerSourceWatch`, no modifica `_hashes` y no devuelve una declaraci?n propia de `fresh`. Cada carga publicada produce un recibo con identidad del m?dulo, ruta y SHA-256 de los bytes compilados. `runner_source_snapshot` (`playbook_tool.py:142`) solo entrega ese recibo si corresponde al runner y a la entrada can?nica actuales; durante construcci?n devuelve indisponible sin esperar al import.

El observador normal (`tools/dayz_mcp/server_freshness.py:91`) compara el disco con ese recibo exclusivamente para el runner permitido. Las referencias de arranque de los dem?s m?dulos siguen siendo inmutables. Una generaci?n nueva fuerza lectura de disco incluso con el mismo `stat`; una generaci?n que cambia durante la observaci?n produce `unknown`. `bridge_status` sigue usando su camino habitual (`server.py:3344`). El recibo acredita esta carga concreta; no convierte la vigilancia general en una atestaci?n de todo el bytecode del proceso.

| Restricci?n | Implementaci?n | Prueba ejecutada |
|---|---|---|
| Solo ese m?dulo, por nombre | Lista blanca adicional dentro de `reload_runner`, `playbook_tool.py:158`; ruta fija `PLAYBOOKS_DIR / "runner.py"` en `:93`. | `test_playbook_reload.py:138` rechaza m?dulos del servidor, adaptador y ControlClient, alias, traversal y tipos incorrectos antes de cargar. `:146` comprueba par?metro obligatorio, const del schema y rechazos MCP. |
| No recargar con un playbook activo | Contador compartido y admisi?n bajo el mismo lock, `playbook_tool.py:174`; cubre todo `execute_playbook_run`, `:339`. | `test_playbook_reload.py:158` bloquea pasos reales del runner en dos apps: rechaza con `active_runs=2`, sigue rechazando con uno y permite recargar al terminar el ?ltimo. Los dos runs devuelven la implementaci?n anterior. |
| Cubrir preparaci?n, error y cancelaci?n | El contexto abarca lectura, validaci?n, ejecuci?n y resultado; decremento en `finally`. | `test_playbook_reload.py:219` intenta recargar durante la lectura s?ncrona del TOML; `:194` cancela un run activo; `:213` fuerza error de schema. La admisi?n se libera. |
| Fallo a mitad sin publicar un runner parcial | Restauraci?n del import slot y publicaci?n solo en la rama de ?xito, `playbook_tool.py:113`. | `test_playbook_reload.py:240` ejecuta asignaciones del candidato y despu?s lanza `RuntimeError`: quedan las identidades del m?dulo, recibo y runner anteriores, y el siguiente playbook sigue ejecutando `old`. Una carga corregida recupera `new`. |
| Errores de carga expl?citos | `ToolError` sin propagar texto arbitrario de la excepci?n; comprobaci?n de API y fuente. | `:253` sintaxis; `:260` archivo desaparecido; `:268` SystemExit; `:275` API incompatible; `:285` fallo inicial y retry; `:312` edici?n del archivo durante `exec_module`. |
| Concurrencia durante construcci?n | Try-lock para recarga/admisi?n y recibo no bloqueante, `playbook_tool.py:142`, `:158`, `:174`. | `test_playbook_reload.py:325` pausa `exec_module` con una barrera de hilo: el observador responde `unknown` y las llamadas MCP de recarga y ejecuci?n responden `load_in_progress` antes de liberar la barrera. |
| C?digo nuevo realmente ejecutado y vuelta a fresh | Compilaci?n desde bytes y lectura ordinaria del recibo por el observador. | `test_playbook_reload.py:105` usa una sesi?n MCP real en memoria: `old/fresh ? old/stale ? reloaded ? fresh ? new/PASS`. Conserva la app, handler, identidad del runtime, schemas, objeto observador y sus hashes de arranque. `:297` demuestra con un control positivo que el cargador est?ndar ejecutar?a el `.pyc` viejo con mismo tama?o/mtime, mientras la recarga ejecuta `new`. |
| No blanquear m?dulos ajenos ni cambios posteriores | Solo el runner tiene recibo de carga; los dem?s conservan la referencia de arranque. | `test_playbook_reload.py:383` conserva stale de otro m?dulo; `:395` detecta una edici?n posterior y aplica una segunda recarga; `:354` detecta cambio de generaci?n durante observaci?n; `:373` rechaza una entrada can?nica sin recibo v?lido. |

Se deniega adem?s `playbook_reload` como paso de un playbook (`playbook_tool.py:45`; test `:231`). El caso con dataclass a nivel de m?dulo tambi?n pasa (`test_playbook_reload.py:408`).

**Lectura del resultado:** si la llamada entr? con c?digo stale, su respuesta de recarga conserva el aviso de entrada aunque la carga haya terminado bien. Esto es deliberado: `_call_marker` une ambas observaciones (`server_freshness.py:174`). La llamada siguiente a `bridge_status` decide la frescura actual. El ?xito de recarga tiene `isError=false`; los rechazos y fallos tienen `isError=true`.

Se actualizaron las cuatro listas de inventario en `tools/tests/fixtures/effective_schema_v5/profile_inventory.json:20` y sus expectativas independientes en `tools/tests/test_effective_schema_catalog.py:26`. Dos fixtures anteriores necesitaban restaurar el nuevo estado de carga: `test_server_freshness.py:391` ahora restaura tambi?n el recibo; `test_playbook_tool.py:381` restaura el singleton al salir. Se conservaron sus aserciones.

## 2. T2 ? NO

**No se implementa una tool de salida del proceso stdio.** El helper existente no satisface el contrato propuesto:

1. **No es un cierre graceful de una llamada viva.** `tools/dayz_mcp/server.py:5389` explica que el peer ya desapareci?; intenta `runtime.stop_loopback()` y termina mediante `os._exit(0)` en `:5397`. Sus usos en `run` corresponden a los watchdogs del modo embedded, `:5418`. El modo daemon toma su rama propia en `:5402`; el hecho de que sea independiente no acredita la entrega del resultado al host.
2. **Retornar desde la tool no prueba que la respuesta haya salido.** En el SDK instalado, `tools/.venv-mcp/Lib/site-packages/mcp/server/lowlevel/server.py:770` espera al handler y solo en `:800` llama a `message.respond`. Otro task escribe y hace flush en `mcp/server/stdio.py:75`, `:80` y `:81`. Ni el retorno ni un timer arbitrario dan al helper existente un acuse del flush de esa respuesta. No se ejecut? `os._exit` para probarlo.
3. **Una foto de idle no cierra la admisi?n.** El SDK admite requests concurrentes en `lowlevel/server.py:678`. Los campos locales de lease, ticket y operaci?n existen en `tools/dayz_mcp/control_client.py:161`, y se exponen en `server.py:1234`; no son una barrera global de cierre. `dayz_test_run` mantiene su ticket y task de claim en variables locales (`server.py:3546`), espera caja en `:3560` y crea heartbeat en `:3579`, antes de entrar en el lock que cubre la ejecuci?n en `:3585`. Comprobar solo los campos de ControlClient o un lock libre no acredita que no exista una llamada admitida en ese tramo.

Ofrecer esa garant?a exigir?a un protocolo de cierre que cercase admisi?n y acreditase el env?o por el transporte, con pruebas del ciclo del host. No lo sustituyo por un suicidio diferido. No se promete reapertura autom?tica ni se modifica el remedio general `reopen_mcp_client`. Las condiciones de rechazo de T2 no se declaran probadas: no hay implementaci?n de T2.

## 3. Tests y gate

**22 tests nuevos**, todos de `tests.test_playbook_reload.PlaybookReloadTest`; una entrada por test:

- `test_wire_reload_changes_behavior_and_normal_status_without_reanchoring` ? `tools/tests/test_playbook_reload.py:105`.
- `test_allowlist_rejects_all_other_targets_before_loading` ? `tools/tests/test_playbook_reload.py:138`.
- `test_wire_requires_exact_explicit_module` ? `tools/tests/test_playbook_reload.py:146`.
- `test_in_flight_runs_across_apps_refuse_until_last_run_finishes` ? `tools/tests/test_playbook_reload.py:158`.
- `test_cancellation_releases_execution_admission` ? `tools/tests/test_playbook_reload.py:194`.
- `test_schema_error_releases_execution_admission` ? `tools/tests/test_playbook_reload.py:213`.
- `test_admission_covers_synchronous_playbook_loading` ? `tools/tests/test_playbook_reload.py:219`.
- `test_playbook_cannot_invoke_reload_as_a_step` ? `tools/tests/test_playbook_reload.py:231`.
- `test_mid_execution_failure_restores_module_and_allows_recovery` ? `tools/tests/test_playbook_reload.py:240`.
- `test_syntax_failure_preserves_old_runner` ? `tools/tests/test_playbook_reload.py:253`.
- `test_missing_source_preserves_old_runner_and_reports_unknown` ? `tools/tests/test_playbook_reload.py:260`.
- `test_module_level_exit_is_an_error_and_keeps_old_runner` ? `tools/tests/test_playbook_reload.py:268`.
- `test_invalid_runner_api_is_not_published` ? `tools/tests/test_playbook_reload.py:275`.
- `test_failed_first_load_cleans_import_slot_and_can_retry` ? `tools/tests/test_playbook_reload.py:285`.
- `test_reload_bypasses_valid_old_pyc_and_refreshes_same_stat_cache` ? `tools/tests/test_playbook_reload.py:297`.
- `test_source_change_during_exec_rejects_candidate` ? `tools/tests/test_playbook_reload.py:312`.
- `test_concurrent_reload_and_run_refuse_during_module_construction` ? `tools/tests/test_playbook_reload.py:325`.
- `test_generation_change_during_observation_is_unknown_then_fresh` ? `tools/tests/test_playbook_reload.py:354`.
- `test_unaccredited_import_slot_replacement_cannot_turn_fresh` ? `tools/tests/test_playbook_reload.py:373`.
- `test_reload_does_not_clear_other_modules_drift` ? `tools/tests/test_playbook_reload.py:383`.
- `test_subsequent_edit_is_stale_and_second_reload_uses_latest_bytes` ? `tools/tests/test_playbook_reload.py:395`.
- `test_reload_supports_module_level_dataclasses` ? `tools/tests/test_playbook_reload.py:408`.

Focal conjunto final: los siete m?dulos `test_playbook_reload`, `test_playbook_tool`, `test_server_freshness`, `test_effective_schema_catalog`, `test_effective_schema_core`, `test_mcp_tools` y `test_wire_coercion_census`. Salida literal de [ronda3-focused-final.log](ronda3-focused-final.log):

```text
Ran 171 tests in 20.076s

OK
```

Exit code del focal: **0**. Se us? `tools/.venv-mcp/Scripts/python.exe -B -m unittest`, sin pytest.

**Gate completo literal ejecutado:**

```powershell
powershell -ExecutionPolicy Bypass -File tools\run-tests.ps1
```

Salida literal final, [ronda3-full-suite.log](ronda3-full-suite.log) y [ronda3-suite-summary.txt](ronda3-suite-summary.txt):

```text
Ran 3313 tests in 258.372s

FAILED (failures=5, errors=79, skipped=28)
Exit code: 1
```

Son los **3291 de base + 22 nuevos**. No hubo skips ni relajaciones de timeout a?adidos por esta lane.

**Diferencia contra RONDA2.** Se compararon identidades de caso y subtest, eliminando los cortes de l?nea del log PowerShell, no solo totales: [ronda3-failure-comparison.json](ronda3-failure-comparison.json). Los 81 encabezados de fallo/error de R2 siguen presentes. Se a?aden:

- `test_install_mcp.PublicToolCountDocsTest.test_readme_tool_count_matches_instantiated_app`.
- `test_install_mcp.PublicToolCountDocsTest.test_architecture_tool_count_matches_instantiated_app`.
- `test_daemon_spawn_branch.DaemonEventRecordTest.test_a_held_writer_lock_does_not_hold_up_the_caller`: **WinError 145 al limpiar el TemporaryDirectory**, `test_daemon_spawn_branch.py:436`. No fall? aqu? la aserci?n de presupuesto temporal. El reintento aislado pas?: `Ran 1 test in 0.063s / OK`, [ronda3-writer-cleanup-recheck.log](ronda3-writer-cleanup-recheck.log). No se ha aislado su causa ni se usa ese pase para aprobar la suite.

Los tres FAIL ya presentes en R2 siguen en startup/election de daemon, representaci?n de rutas en `test_dayz_tools_paths` y lectura del bundle en `test_lifecycle_reconcile`. Los errores restantes conservan sus casos de R2, incluidos accesos a procesos, bundles y autoridad de rutas. Tracebacks completos: [ronda3-suite-failures.txt](ronda3-suite-failures.txt). Ning?n FAIL/ERROR final pertenece a los siete m?dulos del focal.

**Los dos fallos documentales son responsabilidad de esta superficie nueva, no se atribuyen al sandbox.** El registro ahora tiene 61 tools en la configuraci?n de esos tests y los documentos publican 60. Cambiarlos aqu? incumplir?a ?Fuera de tools/ no se escribe, salvo tu carpeta de entrega?. Se entrega el ajuste completo, **sin aplicar**, en [ronda3-docs-counts-PENDING.patch](ronda3-docs-counts-PENDING.patch): encabezado y recuento de README, nombre de la nueva tool en su lista, y ambos recuentos actuales de arquitectura. El hist?rico de 11 tools se conserva.

Los dos tests originales pasaron contra el texto propuesto en memoria, sin editar sus aserciones ni los documentos del repo: `Ran 2 tests in 0.892s / OK`, [ronda3-proposed-docs-check.log](ronda3-proposed-docs-check.log). Esto valida el parche propuesto; **el gate del ?rbol sigue rojo hasta que se integre y se vuelva a ejecutar**.

La primera suite de esta lane, antes de hacer no bloqueante la observaci?n durante construcci?n, dio `Ran 3313 tests in 250.477s / FAILED (failures=5, errors=78, skipped=28)`; est? en [ronda3-full-suite-pre-nonblocking.log](ronda3-full-suite-pre-nonblocking.log). El test de limpieza sali? ok en esa corrida. No se oculta ninguna de las dos.

Los hashes antes/despu?s del gate final coinciden: [ronda3-tested-source-hashes.json](ronda3-tested-source-hashes.json), `changed_during_gate=[]`. Tambi?n se contrastaron de nuevo despu?s del gate. `inbox.py`, `test_pipeline_feedback.py` y `playbooks/runner.py` conservan hashes y mtimes del comienzo de la lane.

## 4. LO QUE NO PUDE VERIFICAR

- **Gate global verde.** Quedan los dos ajustes documentales sin aplicar y los fallos descritos. Los errores de acceso no se eludieron con elevaci?n ni se repararon superficies ajenas.
- **Activaci?n en el MCP que ya estaba abierto.** Una tool nueva y los cambios de adaptador/observador requieren que el anfitri?n cargue esta versi?n en un proceso nuevo. No pueden instalarse retrospectivamente con una tool que el proceso viejo a?n no tiene.
- **Ejecuci?n de la recarga en el proceso de producci?n ni estado final del run `bdd0d88f`.** Se trabaj? con fixtures temporales, un puente simulado y sesiones MCP en memoria. No se lanz? DayZ, no se actu? sobre Steam ni el registro, y no se mat? ni reinici? el daemon, la partida o el MCP vivos.
- **Estado final autoritativo de lease/cola.** La ?nica consulta `session_status` fue rechazada con `MCP tool call requires approval, but approval policy is never`. No se adquiri? lease porque no hubo secuencia mutante sobre el juego. No se certifica un cierre de producci?n.
- **Reapertura del host, entrega/flush y barrera global de salida.** T2 queda en NO precisamente porque no se acreditaron esas garant?as.
- **Sem?ntica arbitraria de c?digo nuevo.** La recarga no es un sandbox ni una transacci?n de todos los efectos externos que pudiera introducir un futuro runner. Se acreditan la construcci?n/publicaci?n del m?dulo y sus consumidores actuales. Un import que nunca termina no tiene cancelaci?n forzada: las llamadas reciben estado desconocido/rechazo mientras siga cargando.
- **L?mites heredados de frescura.** Sin una recarga expl?cita, un cambio que conserve mtime, tama?o e identidad puede escapar a la cach? de `stat`, igual que en R2. No se acredita ausencia de monkeypatches, efectos externos ni modificaciones transitorias entre observaciones.

## 5. COMO LO COMPRUEBO YO

**Comprobaci?n offline completa de T1; no requiere reiniciar tu propio MCP ni contactar el daemon:**

```powershell
Set-Location -LiteralPath 'C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev'
powershell -ExecutionPolicy Bypass -File tools\run-tests.ps1 test_playbook_reload
```

Decide `Ran 22 tests ...`, `OK` y `Exit code: 0`. El int?rprete nuevo es el de unittest, no tu servidor MCP. El m?dulo copia el runner real a un directorio temporal y prueba all? los cambios. La secuencia positiva est? en `test_playbook_reload.py:105`; el bloqueo con dos runs en `:158`; el fallo a mitad en `:240`; el bloqueo durante construcci?n en `:325`. No hay que editar el runner de producci?n ni fabricar actividad en la partida.

Para verificar el parche documental pendiente sin escribir fuera de la entrega:

```powershell
& 'tools\.venv-mcp\Scripts\python.exe' -B 'reviews\mcpreload-2026-09-09\ronda3-check-proposed-docs.py'
```

Deciden `Ran 2 tests ... / OK` y el mensaje `Proposed text only; repository documentation was not modified.`. El comprobador rechaza si han cambiado los hashes del origen o de la propuesta. Este paso no aplica el parche. El receptor debe integrar esos cambios documentales con el alcance correspondiente antes de esperar que pasen en la suite real.

Despu?s, ejecutar el gate completo original. Solo `OK` y exit **0** acreditan su cierre; los recuentos rojos conservados en esta entrega no lo acreditan.

**Comprobaci?n futura en una sesi?n real, con esta versi?n ya cargada:** consultar tools/list y localizar `playbook_reload` con `module` requerido. Tras una edici?n deliberada del runner y sin playbooks activos, llamar `playbook_reload` con `{"module":"dayz_playbook_runner"}`; decide `isError=false` y `status=reloaded`. En la llamada siguiente a `bridge_status`, el runner debe desaparecer de `server_modules.stale` y `unreadable`, sin errores de observaci?n. Si las dem?s fuentes est?n frescas, el estado global ser? `fresh` y `tool_registry_source_stale=false`. Una marca stale en la respuesta de recarga puede corresponder a la observaci?n de entrada, como prueba el test.

**Si la tool no aparece, el receptor no puede solucionar esa primera activaci?n reiniciando su propio servidor desde aqu?.** Hace falta que el anfitri?n o un operador externo cierre/reabra el cliente MCP en un momento apropiado. No se ofrece ninguna tool que prometa hacerlo ni se ha ejecutado ese procedimiento contra la sesi?n viva.

## 6. Entrega y l?mites de escritura

Ocho archivos cambiados bajo `tools/`: tres de producci?n, cuatro tests existentes/fixtures de inventario y el nuevo m?dulo de tests. Los paths exactos est?n en el patch de c?digo y en el manifiesto de hashes. `README.md` y `dayz-mcp-architecture.md` solo tienen propuestas dentro de esta carpeta.

**Sin commit:** git estaba prohibido; el receptor revisa, ejecuta su gate e integra. No se tocaron los cambios ajenos en `tools/dayz_mcp/inbox.py` ni `tools/tests/test_pipeline_feedback.py`.

Este informe es el handoff autorizado. Se aplic? `post-session` dentro del alcance de escritura: no se actualiz? el vault, memoria, buzones ni el HANDOFF compartido. El receptor puede enrutar desde aqu? los l?mites de entorno y el pendiente documental.
