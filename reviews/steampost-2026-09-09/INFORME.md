# 1. T1

Implementado: `steam_pid_repair_reason` y `steam_restart_fallback` se copian en el informe com?n de `dayz_test_run`, tanto si la remediaci?n devuelve ?xito como fallo: `tools/dayz_mcp/dayz_test_tool.py:1438` y `:1441`.

El informe llega a las cuatro salidas existentes: fallo de Steam (`:1480`), rechazo por run no extensible (`:1512`), rechazo de reemplazo de cliente (`:1585`) y resultado del ejecutor (`:1623`). Los tests recorren esas cuatro salidas.

Solo se a?aden esas dos claves p?blicas. `SteamSessionResult` conserva sus cuatro campos y su evaluaci?n anterior; no se publica `active_user`, rutas de DLL ni los diagn?sticos internos adicionales del reparador. Un resultado legacy sin los campos nuevos recibe `None` / `False`. Si la funci?n lanza una excepci?n, esa pareja es el valor de compatibilidad y **no permite reconstruir la rama que alcanz? antes de lanzar**; permanece `steam_remediation_error` con la clase de excepci?n.

Cambios propios: los dos m?dulos productivos anteriores, dos fixtures existentes y `tools/tests/test_steampost_readiness.py`. [Diff de los archivos existentes](changes.diff), [copias previas y hashes](source-hashes.json). El test nuevo se entrega como archivo completo. Se conservaron los finales de l?nea originales de cada archivo.

# 2. T2

**Entrego una mitigaci?n de arranque, no una prueba de que Steam est? sirviendo IPC a DayZ. T2 no queda acreditado como arreglo definitivo.** La investigaci?n acotada no encontr? una se?al sin DLL que garantice la inicializaci?n de Steamworks dentro del juego. S? encontr? un observable de arranque que discrimina el fallo medido, sin inventar una edad m?nima.

Observable elegido: una l?nea completa `System startup time: ? seconds` en los ?ltimos 128 KiB de `logs/console_log.txt`, fechado dentro de la vida del proceso Steam comprobado. Lo lee `SteamPreflightProvider.steam_startup_complete(pid)` (`tools/dayz_mcp/steam_preflight.py:89`); la implementaci?n Windows est? en `:156`, y el parser temporal en `:202`.

La sonda abre un handle de consulta (`0x1000`), obtiene creaci?n e imagen desde ese mismo handle, deriva el log de la carpeta de ese `steam.exe`, lee una cola acotada y comprueba que el proceso sigue activo. El handle se cierra tambi?n al fallar. `GetProcessTimes` y `GetExitCodeProcess` tienen firmas verificadas en las p?ginas oficiales de Microsoft, consultadas el 09-09-2026: [tiempos y FILETIME](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes), [estado de salida](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getexitcodeprocess). Binding: `tools/dayz_mcp/steam_preflight.py:234`.

Ambas ramas pasan por `_wait_for_steam_startup` (`:587`) despu?s de reparar/verificar el registro. Cada sondeo exige registro v?lido, el mismo PID esperado y un ?nico Steam enumerado; tras un positivo vuelve a comprobar la sesi?n. Un fallo de este gate termina la remediaci?n, sin iniciar otro ciclo de reinicio. Los hosts legacy sin escritor mantienen su respaldo de reinicio cuando el provider s? ofrece la nueva sonda. Un provider sin la capacidad falla antes de cualquier mutaci?n (`:566`).

**Estado concreto que dice ?todav?a no sirve? a efectos de esta mitigaci?n:** PID y cuenta v?lidos, imagen `steam.exe` viva, pero todav?a no hay marcador de fin de arranque fechado en la vida de ese proceso. La sonda devuelve `False`, aunque `evaluate_steam_session` devuelva `error_code=None`. Tambi?n devuelve `False` con solo un marcador anterior al proceso actual. No se interpreta el tiempo transcurrido como un positivo.

Evidencia le?da, conservada con los identificadores de cuenta eliminados en [observations.json](observations.json):

| Fuente | Hecho comprobado |
|---|---|
| `C:/Program Files (x86)/Steam/logs/bootstrap_log.txt:28228` | Arranque de Steam a las **12:53:04**; la verificaci?n de archivos ya termina en ese segundo (`:28236`). |
| `C:/Program Files (x86)/Steam/logs/connection_log.txt:6276` | A las **12:53:16** todav?a programa la conexi?n de login. |
| Mismo log, `:6306` y `:6307` | Respuesta de login y procesamiento completo a las **12:53:17**. |
| `C:/Program Files (x86)/Steam/logs/console_log.txt:2264` | **12:53:18**, `System startup time: 13.36 seconds`. Es posterior al arranque fallido de DayZ de las 12:53:15. |
| `_client/profiles/DayZDiag_x64_2026-09-09_12-53-15.RPT:2` | **549 bytes**, solo cabecera, cero l?neas ENGINE. |
| `_client/profiles/DayZDiag_x64_2026-09-09_12-55-24.RPT:2` | **48724 bytes**, misma ruta de exe y misma l?nea de argumentos registrada. Primera ENGINE en `:10`, a las **12:55:26.302**. |

Precisi?n temporal: el segundo RPT se llama `12-55-24`, pero su cabecera dice `Current time: 12:55:26`; los 0,302 s se miden desde esa cabecera, no desde la creaci?n del proceso. Las horas 12:53:04 y 12:55:24 distan **140 s**, por lo que ?4 min? no se usa para fijar un umbral.

Candidatos y l?mites del descarte:

- **ActiveUser positivo:** ya lo exige el fast path; el contraejemplo del encargo tiene registro coherente. No a?ade un observable de servicio. La evidencia anterior `evidence/20260908-steam-console-lifecycle/steam-api-sequence.json:4` tambi?n conserva el booleano positivo. No reinterpret? ni publiqu? el AccountID.
- **SteamClientDll / SteamClientDll64 y existencia de archivos:** comprob? que las DLL instaladas existen. Los fallos del d?a 8 ya ten?an cargada `steamclient64.dll` y las mismas DLL auxiliares: `evidence/20260908-steam-console-lifecycle/minidump-steam-findings.md:25`. Existencia/carga no acredita servicio. No pude leer los valores actuales de ActiveProcess desde este contexto: su HKCU no contiene la clave y el acceso a la clave de usuario cargada fall?. No afirmo haber verificado esos dos valores durante el fallo de hoy.
- **Edad m?nima:** descartada como criterio. Las dos observaciones no fijan un umbral. El marcador se acepta en cuanto existe; los 180 s son un presupuesto m?ximo, no un tiempo m?nimo que se duerme.
- **Login de red / WebUI:** sus logs aportan secuencia temporal, pero no prueban IPC del juego. La WebUI ya tiene `Initialized` a las 12:53:04 (`transport_steamui.txt:1046`); por s? sola habr?a pasado demasiado pronto. Tampoco convierto conexi?n a servidores de Steam en requisito universal para servicio local.
- **Steamworks:** Valve documenta `SteamAPI_Init` como adquisici?n de interfaces y enumera dependencias de AppID, licencia y contexto de usuario; [documentaci?n oficial](https://partner.steamgames.com/doc/api/steam_api#SteamAPI_Init). `CreateSteamPipe` y `ConnectToGlobalUser` son APIs de la interfaz nativa, no un protocolo Python independiente: [ISteamClient](https://partner.steamgames.com/doc/api/ISteamClient#CreateSteamPipe). No cargu? ni ejecut? esas DLL en esta sesi?n.

Sobre la conclusi?n heredada `SteamAPI_IsSteamRunning`: **es falsa la simplificaci?n de que sea la ?nica condici?n del juego; no puedo decidir qu? condici?n fall? hoy ni c?mo implementa la DLL esa funci?n.** Rele? el desensamblado guardado en `C:/Users/guill/AppData/Local/Temp/dayz-steam-investigation-20260908/dayz-steam-disasm-callees.txt:74`: se ven Init, su reintento, RestartAppIfNecessary y despu?s IsSteamRunning (`:99` y `:138`); el wrapper tiene una guarda previa (`:141`). El informe `evidence/20260908-steam-console-lifecycle/dayz-native-steam-condition.md:33` registra adem?s sondas externas positivas con clientes que fallaban, como observaci?n de la sesi?n anterior, no una reproducci?n propia. El [header de Valve](https://raw.githubusercontent.com/ValveSoftware/source-sdk-2013/master/src/public/steam/steam_api.h) declara IsSteamRunning pero no demuestra que su implementaci?n solo lea el DWORD.

# 3. Presupuesto y fallo

`tools/dayz_mcp/steam_preflight.py:28` conserva 15 s para el apagado y 20 s para el registro del respaldo. `:30` a?ade **hasta 180 s** para el marcador, sondeando cada **0,2 s**. Fast path: hasta 180 s de espera adicional. Respaldo: hasta **215 s de esperas programadas** en total. Las llamadas locales de SO/archivo se hacen s?ncronamente; estos presupuestos acotan el polling y no interrumpen una llamada de SO bloqueada. `_wait_until` (`:408`) rechaza un positivo que llega despu?s del deadline y no hace una lectura adicional para promoverlo.

Todos los fallos nuevos usan el `error_code` p?blico existente **`steam_session_stale`** y `steam_remediated=false`:

| `steam_remediation_reason` | Condici?n |
|---|---|
| `startup_probe_unavailable` | Provider legacy sin m?todo callable; inmediato y antes de mutar. |
| `startup_timeout` | No apareci? un marcador v?lido dentro del plazo, o el positivo lleg? tarde. |
| `startup_probe_failed` | La ?ltima sonda falla al leer/probar, o devuelve un tipo distinto de bool. |
| `startup_session_changed` | La ?ltima observaci?n no acredita la misma sesi?n/PID ?nico o esta cambia durante la sonda. |

El plazo es ?nico para este gate: ni las excepciones transitorias ni los cambios de PID lo reinician. Se conserva la ?ltima raz?n observada. Los errores anteriores del apagado, relanzado y registro mantienen sus razones. Un timeout de arranque no marca `steam_left_down=true`: no acredita que Steam haya quedado apagado.

# 4. Tests

Los fixtures anteriores modelan Steam ya inicializado y ahora implementan la capacidad expl?citamente: `tools/tests/test_steamfastpath_repair.py:57` y `tools/tests/test_steam_preflight.py:307`. Sus tests originales se conservan.

Tests a?adidos, uno por l?nea:

- `tools/tests/test_steampost_readiness.py:52` ? `test_coherent_registry_without_startup_marker_times_out`.
- `tools/tests/test_steampost_readiness.py:65` ? `test_applied_pid_repair_waits_for_startup_marker`.
- `tools/tests/test_steampost_readiness.py:77` ? `test_already_correct_pid_waits_for_startup_marker`.
- `tools/tests/test_steampost_readiness.py:87` ? `test_restart_waits_after_registry_match_and_preserves_branch`.
- `tools/tests/test_steampost_readiness.py:98` ? `test_restart_with_coherent_registry_but_no_marker_fails`.
- `tools/tests/test_steampost_readiness.py:108` ? `test_already_completed_startup_does_not_sleep`.
- `tools/tests/test_steampost_readiness.py:115` ? `test_legacy_provider_without_capability_fails_before_mutation`.
- `tools/tests/test_steampost_readiness.py:135` ? `test_probe_error_fails_closed_without_private_diagnostics`.
- `tools/tests/test_steampost_readiness.py:144` ? `test_non_boolean_probe_result_cannot_become_success`.
- `tools/tests/test_steampost_readiness.py:154` ? `test_probe_can_recover_from_transient_read_error_within_budget`.
- `tools/tests/test_steampost_readiness.py:160` ? `test_probe_success_after_deadline_is_rejected_without_extra_read`.
- `tools/tests/test_steampost_readiness.py:171` ? `test_registry_change_during_probe_is_not_accepted`.
- `tools/tests/test_steampost_readiness.py:183` ? `test_dead_or_additional_process_during_probe_cannot_pass`.
- `tools/tests/test_steampost_readiness.py:268` ? `test_current_process_marker_is_accepted_through_query_only_handle`.
- `tools/tests/test_steampost_readiness.py:274` ? `test_same_registry_and_log_without_current_marker_is_not_ready`.
- `tools/tests/test_steampost_readiness.py:277` ? `test_reused_pid_with_new_creation_cannot_reuse_old_completion`.
- `tools/tests/test_steampost_readiness.py:281` ? `test_future_malformed_or_partial_marker_is_not_ready`.
- `tools/tests/test_steampost_readiness.py:293` ? `test_log_reads_are_bounded_and_truncated_first_line_is_discarded`.
- `tools/tests/test_steampost_readiness.py:300` ? `test_missing_or_denied_log_raises_and_releases_handle`.
- `tools/tests/test_steampost_readiness.py:308` ? `test_wrong_image_or_exited_process_cannot_pass`.
- `tools/tests/test_steampost_readiness.py:317` ? `test_native_query_failures_release_only_acquired_handle`.
- `tools/tests/test_steampost_readiness.py:331` ? `test_invalid_pid_is_rejected_without_native_access`.
- `tools/tests/test_steampost_readiness.py:337` ? `test_native_signatures_preserve_handle_width_and_filetime_pointers`.
- `tools/tests/test_steampost_readiness.py:399` ? `test_success_copies_fast_and_restart_branch_without_private_values`.
- `tools/tests/test_steampost_readiness.py:410` ? `test_failed_remediation_copies_both_branches_and_never_launches`.
- `tools/tests/test_steampost_readiness.py:423` ? `test_non_extensible_refusal_preserves_remediation_branch`.
- `tools/tests/test_steampost_readiness.py:432` ? `test_client_replacement_refusal_preserves_remediation_branch`.
- `tools/tests/test_steampost_readiness.py:447` ? `test_legacy_result_defaults_are_explicit_without_attribute_errors`.
- `tools/tests/test_steampost_readiness.py:453` ? `test_exception_reports_unknown_branch_and_does_not_launch`.
- `tools/tests/test_steampost_readiness.py:461` ? `test_no_remediation_does_not_add_branch_fields`.

Las pruebas nuevas usan reloj, registro, procesos, archivos y lanzamiento inyectados. La implementaci?n Windows de la sonda corre contra un kernel32 falso y un stream en memoria; el binding ctypes se prueba con una DLL falsa. Los tests anteriores de escritura contin?an usando winreg falso. El observador para el receptor solo se compil?, no se ejecut? contra Steam.

Resultados focales: **30/30 nuevos**, **17/17 preflight**, **28/28 fast path**, **5/5 lote T2**, **62/62 consumidor**: **142 tests, todos verdes**. Logs separados en esta carpeta.

Discriminaci?n contra BEFORE: [verify_regressions.py](verify_regressions.py) carga las dos copias previas sin sustituir archivos del ?rbol. Nueve tests de conducta dan **12 fallos de aserci?n por los subcasos, cero errores** contra BEFORE; los nueve pasan con el c?digo entregado. [Rojo](regressions-before.log), [verde](regressions-after.log). No se contabilizan AttributeError como regresiones de conducta.

Gate completo ejecutado exactamente con el int?rprete del wrapper, sin modificar `tools/run-tests.ps1`:

```powershell
powershell -ExecutionPolicy Bypass -File tools\run-tests.ps1
```

Recuento literal de [suite.log](suite.log):

```text
Ran 3247 tests in 305.199s

FAILED (failures=2, errors=78, skipped=28)
Exit code: 1
```

**GATE GLOBAL: ROJO. No se cumple el criterio de entrega con suite entera verde.** El recuento es la base 3217 m?s 30 tests nuevos, sin reducir la selecci?n.

Clasificaci?n de las 80 incidencias en [suite-blockers.json](suite-blockers.json):

- **33** contienen acceso denegado al escanear procesos, con `psutil.AccessDenied` ? `process_scan_incomplete` (`identity_migration.py:734`). Incluyen el fallo del test de elecci?n de daemon. No gestion? los procesos que el sandbox no deja inspeccionar.
- **28** contienen denegaci?n de lectura del bundle nativo, incluida `tools/native-launchers/dayz-test-v1/closure-manifest.json`, y el test que no encuentra su segundo hash porque esa lectura es ilegible.
- **19** terminan en `invalid_dayz_test_path_authority`. Su causa concreta no qued? aislada; la funci?n envuelve varios tipos de excepci?n en `tools/dayz_mcp/request_path_authority.py:581`. No los atribuyo todos a una causa de permisos por inferencia.

Control acotado: [verify_suite_blockers.py](verify_suite_blockers.py) ejecuta tres casos de acreditaci?n/lectura de bundle con fuentes BEFORE y AFTER: ambos dan **1 fallo y 2 errores**, con las mismas firmas. [Antes](blockers-before.log), [despu?s](blockers-after.log). Esto demuestra preexistencia en esos controles; no sustituye una suite completa de la base ni una investigaci?n de las otras incidencias. Obtener un gate verde requiere repetirlo en el contexto autorizado del receptor y resolver lo que persista. No alter? ACL, registro sellado, int?rprete ni tests ajenos para cerrar ese gate.

# 5. LO QUE NO PUDE VERIFICAR

- Que el marcador implique que **DayZ**, en su contexto real de lanzamiento, puede inicializar Steamworks. Un Steam que se queda sin responder despu?s de escribir el marcador puede seguir pasando esta mitigaci?n. No es una sonda de salud IPC.
- La condici?n interna concreta del fallo del 09-09 ni que IsSteamRunning lea exclusivamente la clave reparada. No ejecut? DLL, juego ni c?digo mutador de Steam.
- La transici?n real fr?o ? marcador con la implementaci?n nueva. Se reprodujo con dobles y registros hist?ricos; queda el experimento del receptor.
- Que el formato, retenci?n y buffering del log funcionen en todas las versiones. Si rota o el marcador sale de los ?ltimos 128 KiB, se fallar? cerrado aunque Steam est? operativo. La asociaci?n al proceso es temporal: el marcador no lleva PID. Cambios de reloj/zona horaria y escritores simult?neos son l?mites de esa asociaci?n.
- La ruta de remediaci?n cuando el **primer** `evaluate_steam_session` ya pasa: el call-site solo invoca remediaci?n tras un preflight fallido (`tools/dayz_mcp/dayz_test_tool.py:1423`). Esa v?a conserva su contrato; **no recibe este gate**, incluso con opt-in. Un resultado sin campos de remediaci?n no ejercita T2.
- Suite global verde desde este sandbox. Los permisos de procesos y bundle est?n restringidos, y quedan los errores de acreditaci?n sin aislar.

No hay commit ni acciones de git, por encargo. No se modificaron `_client/`, `_server/` ni `HANDOFF.md`. La lectura de RPT fue pasiva. La suite indicada ejecuta tambi?n sus harnesses de daemon en fixtures temporales; no se inici? ni administr? el daemon operativo ni un juego real para esta investigaci?n. No se adquiri? lease ni se llam? `session_status`, que aqu? podr?a activar un cliente/daemon: no hubo secuencia exclusiva de juego. No se actualiz? el vault ni se envi? feedback fuera de la carpeta de entrega por la frontera de escritura del encargo; el receptor puede trasladar este informe y las limitaciones de entorno al buz?n del pipeline.

# 6. COMO LO COMPRUEBO IN-GAME

Procedimiento pendiente del **receptor**, en una ventana de pruebas con Steam libre del uso del usuario. No se ejecut? aqu?. Firmas verificadas: `dayz_test_run` en `tools/dayz_mcp/server.py:3504`, `bridge_status` en `:4756`, `session_status` en `:3449`, `session_acquire_wait` en `:3376` y `session_release` en `:3431`. La secuencia de ownership sigue `C:/Users/guill/ObsidianVault/AI/20_Runbooks/dayz-mcp-agent-session-protocol.md:91`.

1. **Cargar y verificar la entrega.** Comparar los cinco archivos con `source-hashes.json`, repetir la suite completa en el contexto del receptor y cargar estas versiones en su sesi?n MCP mediante su procedimiento de lifecycle autorizado. Un daemon que conserva el m?dulo anterior no prueba esta entrega. Son cambios Python; esta comprobaci?n no necesita cambiar el PBO. No continuar como aceptaci?n mientras el gate global siga rojo.

2. **Fijar el run.** Consultar `session_status` y `bridge_status`. Usar un servidor de pruebas propio, gestionado, `RUNNING_IDLE`, sin cliente sano que deba conservarse; guardar su identificador exacto como `RUN_ID`. Si hay que crear el servidor, hacerlo con `dayz_test_run(project="DayZ_MCP", mode="server", build=false)` y la lista efectiva de mods ya aprobada para ese mundo. Conservarla en la extensi?n de cliente. No adoptar ni cerrar runs ajenos. `dayz_test_run` gestiona su propio lease; no preadquirir otro alrededor.

3. **Observar el arranque [EXACT].** En otra consola de la misma cuenta Windows que Steam, desde la ra?z del proyecto:

   ```powershell
   & .\tools\.venv-mcp\Scripts\python.exe -B .\reviews\steampost-2026-09-09\observe_startup.py --seconds 240
   ```

   El observador es de solo lectura, imprime cambios en JSON con hora/PIDs/booleanos y termina al agotar el plazo. `startup_complete=false` con `registry_passed=true` es el negativo que la clave sola no detecta. `probe_error` o `startup_complete=null` son ausencia de medici?n, no un positivo. No guarda AccountID.

4. **Ejercitar el respaldo en fr?o [EXACT: firma y argumentos; RUN_ID se toma del paso 2].** Cuando la ventana est? libre, el usuario cierra Steam desde su UI; no tocar el DWORD para fabricar una rama. Con el servidor propio preparado y Steam cerrado, ejecutar **una** extensi?n de cliente:

   ```text
   dayz_test_run(project="DayZ_MCP", mode="client", run_id=RUN_ID,
                 build=false, auto_remediate_steam=true)
   ```

   Pasar tambi?n las mismas opciones de mods/puerto/perfil que correspondan al run preparado. Conservar ?ntegro el resultado seguro. Deben aparecer `steam_pid_repair_reason` y `steam_restart_fallback`; para esta preparaci?n se espera `steam_restart_fallback=true`. El observador debe registrar el nuevo PID sin marcador y despu?s el positivo, si Steam completa el arranque. Si solo se observa el estado final, la transici?n fr?a es **INCONCLUSA**; no se inventa una medici?n intermedia. La rama `applied` se puede medir cuando aparezca naturalmente un PID registrado rancio; para forzarla sin ese estado har?a falta una mutaci?n prohibida aqu?. Sus garant?as offline ya est?n cubiertas.

5. **Decidir el resultado sin confundir lanzamiento y juego.** Guardar la l?nea nueva `System startup time` y el RPT nuevo de esa extensi?n. Identificar el PID del cliente por el `RUN_ID` en lifecycle y leer su `StartTime`; no usar el PID del servidor. Las salidas deciden as?:

   - **Mitigaci?n ejercitada y caso positivo:** ambos campos de T1 presentes, `steam_remediated=true`, inicio del cliente posterior al marcador del Steam nuevo, RPT con l?neas ENGINE y cliente vivo. Esperar hasta **360 s** al puente, consultando `bridge_status`; exigir `ready.ready=true` en ese run y cliente conectado. `process_alive`, `bridge_ready` y `reason` son ejes separados en `tools/dayz_mcp/dayz_test_tool.py:558` y `:1089`. Si hacen falta operaciones del bridge, adoptar primero el run con `session_acquire_wait(purpose="Steam startup verification")`, renovar durante la secuencia y liberar al terminar.
   - **Fallo que refuta suficiencia de la mitigaci?n:** `steam_remediated=true` seguido de cliente muerto y RPT solo de cabecera, aunque el marcador exista. Conservar el evento, hora y resultado; T2 sigue abierto. No buscar el texto del di?logo en el RPT.
   - **Fallo de la espera correctamente cerrado:** `steam_remediated=false`, `error_code="steam_session_stale"` y una raz?n `startup_*`; no debe haberse creado un cliente nuevo. Registrar la duraci?n y comprobar que no hubo otro reinicio autom?tico despu?s del timeout.
   - **INCONCLUSO para T2:** no hay campos de remediaci?n, el m?dulo cargado es anterior, la medici?n del observador fue ilegible, o el cliente inicializa el motor pero falla despu?s por otra causa. `status="succeeded"` sin esas observaciones no decide ?xito.

6. **Cierre y criterio de promoci?n.** No relanzar la corrida entera para investigar un cliente muerto. Conservar el run y la evidencia. Cualquier cierre posterior pasa por el lifecycle autorizado; liberar un lease propio y consultar `session_status`. Anotar resultado de T1, transici?n de la sonda y resultado real del cliente por separado. Un caso positivo valida ese arranque; no convierte el marcador en garant?a universal de IPC. Solo el receptor, con suite verde y la comprobaci?n real, puede decidir promover esta mitigaci?n.
