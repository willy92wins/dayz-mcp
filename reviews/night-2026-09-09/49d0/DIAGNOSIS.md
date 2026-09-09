# 49d0 ? d?nde nace native_job_cleanup_incomplete

**Solo diagn?stico; ninguna fuente, daemon, launcher ni sello modificados por este ticket.** El mensaje se emite en el supervisor nativo cuando no puede acreditar el cierre completo del ?rbol que supervisa. No es el c?digo de salida de AddonBuilder, ni significa por s? solo que la caja estaba ocupada o que quedaron procesos vivos.

## Condici?n exacta

`tools/dayz_mcp/native_launcher_backend.py:1496` calcula:

[EXACT, transcripci?n de fuente le?da; no ejecutada]
```python
cleanup_complete = state.active_zero and active_zero_completed
if not cleanup_complete:
    failure = "native_job_cleanup_incomplete"
```

El raise es `NativeLauncherBackendError(failure)` en `:1507`. Hay dos pruebas independientes:

- `state.active_zero`: el mapa `_process_handles` del depurador est? vac?o (`tools/dayz_mcp/native_debug_state.py:66`). `EXIT_PROCESS` prepara su retirada (`:249`), que se confirma despu?s de `ContinueDebugEvent` exitoso en `complete_continue` (`:275`?`:297`); el backend hace ese Continue en `native_launcher_backend.py:1134`.
- `active_zero_completed`: `_wait_active_zero` recibi? de `GetQueuedCompletionStatus` un `JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO` con la completion key del Job esperado (`native_launcher_backend.py:1112`?`:1131`). Un timeout, un error al leer el puerto, o mensajes sin esa coincidencia no dan esa prueba. `_read_job_completion` convierte fallos de lectura en `None` (`:1067`?`:1089`). No consulta una lista de procesos global de la m?quina.

Secuencia final (`:1477`?`:1498`): si el mapa de debug est? vac?o intenta esperar el evento Job; cierra el handle del Job; drena eventos de debug; si falta la prueba del Job vuelve a esperarla; exige la conjunci?n. Cada espera/drenaje usa `_DEBUG_DRAIN_SECONDS=5.0` (`:54`), no un ?nico timeout total de cinco segundos. `close_job` solo cierra el handle y lo pone a cero (`:238`); la key esperada se guard? antes en `job_completion_key` (`:1195`), as? que **no** se compara accidentalmente contra cero por ese cierre.

## Camino hasta el mensaje de la tool

1. `tools/dayz_mcp/server.py:3430` registra `dayz_test_run`; su bloque de ejecuci?n captura excepciones (`:3531`) y publica `_opaque_dayz_test_failure` (`:3538`). Esa funci?n (`:340`?`:354`) compone `dayz_test_failed:<clase>:<code>` para `NativeLauncherBackendError`, sin reenviar detalles locales.
2. `tools/dayz_mcp/dayz_test_tool.py:1213` llama `secure_launcher.execute_secure_launcher_request`; solo despu?s del retorno parsear?a el terminal stdout/stderr del worker (`:1225`). Una excepci?n del backend impide ese parseo normal.
3. `tools/dayz_mcp/secure_launcher.py:161` envuelve el consumer en `execute_native_launcher_transaction`. El consumer llama `native_launcher_backend.launch_registered_native` (`:145`); la transacci?n recoge `consumer_task.result()` (`native_launcher_transaction.py:766`) y conserva el error (`:770`).
4. `launch_registered_native` (`native_launcher_backend.py:1566`) usa un thread ?nico para Create/Wait/Continue; en `:1610` llama `_supervise_created_launcher`. Captura el error de tipo backend y lo entrega al Future (`:1618`/`:1639`). El punto que produce el code sigue siendo `:1498`.
5. La rama que `build=true` a?ade dentro del worker est? en `tools/dayz_mcp/dayz_test_worker.py:633`: valida source, calcula pack_only y manda un frame `BrokerKind.ADDON_BUILDER` (`:645`?`:655`). Espera ok=true, exit_code=0 y pbo_size>0 (`:657`?`:664`); si falla eso, el error normal del worker es `build_failed`, distinto del observado. Con build=false se omite esta rama y contin?a al modo de lanzamiento (`:668`).
6. La supervisi?n trata expl?citamente los descendientes de AddonBuilder: correlaci?n de `CREATE_PROCESS` con notificaci?n Job y anuncio en `native_launcher_backend.py:1362`?`:1404`; un helper sin anuncio solo se acepta con un AddonBuilder activo, ning?n helper activo, menos de 64 helpers y autoridad de imagen positiva (`:1392`?`:1399`, l?mite en `:28`). Ese camino adicional puede diferenciar build=true de build=false.

No he podido abrir el source del broker C++/app empaquetada en `tools/native-launchers/dayz-test-v1`: la b?squeda del sistema devolvi? **Acceso denegado**. El ?ndice/working tree inicial ya mostraba D para sus dos fuentes; no he determinado si es ausencia efectiva, acceso o trabajo de otra sesi?n. No lo restaur?. La ruta Python y la intenci?n del frame del worker est?n verificadas; el manejo interno actual del frame por el ejecutable queda sin verificar.

## Hallazgo de mayor valor: puede ocultar el fallo inicial

La asignaci?n a `failure` en `:1498` es incondicional cuando falla cleanup. Puede sobrescribir `native_debug_gate_rejected` (`:1411`), errores de anuncios (`:1472`, `:1475`), cancelaci?n (`:1298`) u otro error anterior. En particular, la rama que preservar?a `gate_rejection` detallado solo corre si `failure` todav?a es `native_debug_gate_rejected` (`:1503`?`:1506`).

Por tanto, el aislamiento build=true/false del brief acota la ruta adicional, pero no demuestra que el primer fallo sea el drenaje. Un helper de build rechazado por imagen/topolog?a puede provocar el cierre, y luego el fallo de acreditar cleanup puede tapar el rechazo. El c?digo permite esa cadena; **[HIP?TESIS]** para esta corrida.

Tambi?n hay dos consumidores destructivos del completion port: `_wait_job_new_process` (`:1092`) consume notificaciones y devuelve false si ve ACTIVE_PROCESS_ZERO (`:1107`), sin latch compartido; `_wait_active_zero` espera luego ese mensaje otra vez (`:1112`). Una notificaci?n ya consumida es otra **[HIP?TESIS]**, no causa probada: habr?a que demostrar una secuencia real de eventos que la produzca aqu?.

## Discriminadores y propuesta, sin implementar

| Resultado observado al final | Interpretaci?n acotada | Evidencia necesaria |
|---|---|---|
| debug no vac?o | Falta al menos un EXIT_PROCESS/Continue acreditado | PIDs del mapa, ?ltimo evento y Continue, qui?n inici? close_job |
| debug vac?o / Job false | Falta notificaci?n correcta de Job | Mensajes+keys le?dos por ambos waiters, error de lectura, deadline |
| failure anterior distinto | Cleanup tapa el primer fallo | Guardar primary_failure, cleanup_failure y rechazo de imagen/anuncio |
| Build-only helper rechazado | Topolog?a/autoridad de herramientas candidata | Anuncio, PID/PPID, rol de helper, resultado del verificador de imagen |
| Ambos verdaderos | Este code no deber?a emitirse | Verificar c?digo realmente ejecutado y hash del runtime |

[DESIGN] Primer cambio propuesto: conservar por separado error primario y estado de cleanup, con diagn?stico local estructurado que incluya las dos pruebas. Mantener fail-closed: no convertir este error en ?xito, no matar Steam ni procesos ajenos y no relajar autoridad para que pase un helper. Si la captura demuestra consumo anticipado de eventos, centralizar/latch de la evidencia Job por identidad. Si demuestra un hijo realmente vivo, revisar cierre/herencia de handles y supervisi?n de ese hijo. Ampliar cinco segundos a ciegas no decide entre esas causas.

El test existente `tools/tests/test_native_launcher_backend.py:1514` simula falta de EXIT_PROCESS, y en `:1543` exige este error. Lo he **le?do**, no ejecutado: no constituye reproducci?n del build real. Una bater?a futura de dobles deber?a aislar ambos componentes de la conjunci?n y el enmascaramiento del error; esta lane es solo diagn?stico.

## Premisa y l?mites

El problema descrito puede existir con caja libre porque el criterio es el Job del launcher, no la ocupaci?n global. La observaci?n aportada por el brief favorece una divergencia en el camino BUILD, pero ?build=true falla siempre? no queda demostrado por esta lectura ni para todos los proyectos/pack_only/binarizaci?n. Tampoco el code demuestra crash del juego.

`tools/build_native_launcher.py:53`?`:73` confirma `dayz_test_worker.py` dentro de PACKAGED_MODULES; `native_launcher_backend.py` y `native_debug_state.py` no figuran en esa tupla. No toda hip?tesis de fix obliga a cambiar un m?dulo de esa lista, pero eso no autoriza ning?n resellado durante sesiones vivas. No se ha construido el launcher ni se ha alterado el runtime.

No se ejecut? build, juego, daemon, herramientas MCP, cierre de procesos ni consulta del registro. Es restricci?n expl?cita de ESTE brief. Falta traza de la corrida original, primer failure, los dos valores de cleanup, imagen exacta de helpers y comparaci?n contra artefactos cargados. Este diagn?stico acredita emisor/condici?n/rutas y propone el pr?ximo experimento; no certifica causa ra?z ni arreglo.
