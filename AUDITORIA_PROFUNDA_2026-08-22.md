> [!WARNING]
> **STALE (2026-09-12)**
> No es autoridad de producto. El HEAD actual es v1.2 / `origin/main` @ `edc7bb3`. No reabrir hallazgos sin evidencia nueva.

# Auditoría profunda de DayZ_MCP_dev

**Fecha:** 2026-08-22  
**Snapshot auditado:** commit 8d5d34d7f20bd04b4be1d2abd4a72129012c2589, rama master  
**Alcance:** código versionado en HEAD; el estado no rastreado del árbol se examinó por su efecto sobre reproducibilidad y pruebas, pero no se usó como autoridad del producto.  
**Modo:** revisión de solo lectura. El único fichero creado por esta auditoría es este informe.

## Conclusión ejecutiva

El repositorio muestra una inversión seria en controles fail-closed, aislamiento local, fencing de sesiones, auditoría durable y pruebas. No encontré evidencia confirmada de ejecución remota, fuga de credenciales, corrupción persistente ni muerte del proceso atribuible a un defecto del producto. Sí confirmé tres problemas de severidad alta: el watchdog puede vigilar al proceso equivocado, una reparación de lifecycle puede quedar bloqueada permanentemente en repairing y el servidor HTTP permite crecimiento lineal de hilos antes de autenticar.

El balance de hallazgos es:

| Clasificación | Cantidad | Resumen |
|---|---:|---|
| Confirmados — alta | 3 | Watchdog, recuperación lifecycle, agotamiento local de hilos |
| Confirmados — media | 7 | Rutas de herramientas, metadata Python, auditoría asíncrona, autoridad de vehículo, semántica de control, keyfile, reproducibilidad del árbol |
| Confirmados — baja | 4 | Carrera de test, retención de resultados, validación fragmentada, bootstrap no fijado |
| Potenciales / no confirmados | 5 | Parser URL Enforce, pollHz, SDK relocado, estado de freno, efectos in-game pendientes |
| Focos de sobreingeniería | 8 | Funciones monolíticas, DTO universal, puentes duplicados, Win32 duplicado, monkeypatch privado, etc. |

Mi prioridad sería corregir F-01, F-02 y F-03 antes de ampliar funcionalidad. Después abordaría F-04, F-08 y F-09 porque afectan precisamente a los caminos operativos de build y conducción que el producto declara como centrales.

## Cómo se auditó

Se combinaron cinco tipos de evidencia:

1. Lectura de arquitectura, especificación de producto, planes recientes, memoria verificable y checklists DayZ.
2. Inventario del árbol Git y análisis mecánico de tamaño, funciones, imports, secretos, APIs bloqueantes dentro de async, defaults mutables y patrones Enforce de riesgo.
3. Suite completa del árbol actual y una segunda suite construida únicamente con módulos de test rastreados por Git.
4. Lectura manual de caminos críticos: autenticación HTTP, sesiones, lifecycle, watchdog, lanzamiento nativo, build, credenciales, colas/resultados y bridge Enforce servidor/cliente.
5. Experimentos controlados para discriminar hipótesis: repetición aislada del watchdog, inyección de fallo en recuperación lifecycle, sockets TCP ociosos no autenticados, auditoría lenta y retención de resultados.

Las etiquetas usadas son:

- **[CONFIRMADO]**: el mecanismo se leyó y el defecto se reprodujo o se deduce directamente de una contradicción ejecutable.
- **[POTENCIAL / NO CONFIRMADO]**: hay una divergencia real o una superficie sospechosa, pero falta aislar el comportamiento del consumidor cerrado o probarlo in-game.
- **[DESIGN]**: propuesta de cambio; no es un diff listo para aplicar.

Las severidades describen impacto, no certeza. “Alta” no implica exposición remota. “Crash” se reserva aquí para muerte de proceso; no observé ninguno causado por el producto durante esta auditoría.

## Estado de pruebas y reproducibilidad

### Suite completa del árbol actual

Desde tools se ejecutó .\.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .:

- 2.090 tests ejecutados en 305,442 s.
- 3 fallos y 4 skips.
- Fallos:
  - test_bug046_audit_fault_recovery: lectura inmediata de handoff_pending.
  - test_bug046_startup_deadlock: el test no llegó a observar el PID hijo.
  - test_parent_watchdog: el watchdog eligió el launcher y no el abuelo real.

Los dos primeros pasaron 3/3 al aislarlos. El tercero falló 3/3 durante la investigación y volvió a fallar en la pasada final aislada: watched=51460 frente a grandparent=70444, en tools/tests/test_parent_watchdog.py:297.

### Suite estrictamente rastreada

Se construyó una ejecución con los 124 módulos tools/tests/test_*.py presentes en git ls-files:

- 1.754 tests ejecutados en 212,546 s.
- 1 fallo y 4 skips.
- El único fallo fue el watchdog anterior.

Por tanto, HEAD no está verde en esta máquina. Además, README.md:228-230 solo exceptúa como flake conocido test_bug046_startup_deadlock; no documenta el fallo determinista actual del watchdog.

### Controles adicionales

- audit_runtime_http(Path(".")) devolvió cero hallazgos.
- audit_process_creation(Path(".")) devolvió cero hallazgos.
- No aparecieron secretos con los patrones examinados.
- No se detectaron defaults mutables Python ni llamadas bloqueantes obvias dentro de funciones async rastreadas.
- No había ruff, mypy, pyright ni bandit instalados; no se instalaron dependencias durante la auditoría.
- El addon está sparse-excluded del worktree. Se auditó desde los blobs de HEAD con git show/git grep.
- Los chequeos mecánicos Enforce no encontraron ternarios, delete ejecutable, GetGame().IsServer/IsClient, ctx.Read, CallLater ni operadores multilínea al inicio. Las firmas OnInput(float dt) y OnContact(string zoneName, vector localPos, IEntity other, Contact data) coinciden con P:\scripts\3_Game\vehicles\transport.c:252 y P:\scripts\3_Game\vehicles\transport.c:262, y con los overrides vanilla de P:\scripts\4_World\entities\vehicles\carscript.c:1303 y P:\scripts\4_World\entities\vehicles\carscript.c:1454.
- No existe en este repositorio el linter offline script_validator.py recomendado por el checklist. No se hizo PACKONLY, compilación Enforce ni prueba in-game.

## Hallazgos confirmados

### F-01 — [CONFIRMADO] Alta — El watchdog puede vigilar el launcher en vez del ancestro real

**Evidencia.** native_process_snapshot.same_path compara exclusivamente normcase(normpath(...)) en tools/dayz_mcp/native_process_snapshot.py:59-64. orphan_guard.walk_past_redirectors depende de esa igualdad para saltarse launchers en tools/dayz_mcp/orphan_guard.py:303-336, y recibe sys.executable como ruta del redirector en tools/dayz_mcp/orphan_guard.py:488-500. La integración exige que el PID observado sea el abuelo en tools/tests/test_parent_watchdog.py:277-300.

En esta instalación P: es una vista del proyecto y Windows puede anunciar la misma ubicación por una ruta física C:. La comparación textual no resuelve alias de volumen, rutas finales por handle ni identidad de fichero. La prueba aislada falló de forma repetible; el último run tardó 0,381 s y comparó 51460 != 70444.

**Mecanismo e impacto.** El comentario de producción explica que el launcher del venv sobrevive al proceso que realmente interesa vigilar, tools/dayz_mcp/orphan_guard.py:311-319. Si no se reconoce ese launcher, el watchdog queda ligado a un proceso que puede seguir vivo después de morir el ancestro real. El daemon o proceso hijo puede quedar huérfano y conservar el puerto. Es una degradación de disponibilidad y recuperación; no se observó crash.

**[DESIGN] Cambio propuesto.** Sustituir la igualdad textual por una primitiva central de identidad Win32: abrir ambos paths sin seguir reparse points inesperados, comparar identificador de volumen + file ID y conservar como fallback la ruta final canónica del handle. Esa primitiva debe ser la única autoridad para aliases P:/C:.

**Gate verificable.**

- La prueba existente debe pasar con base Python y venv visibles por letras distintas.
- Añadir fixtures positivos “dos nombres, un mismo file ID” y negativos “texto parecido, file IDs distintos”.
- Matar únicamente el abuelo de la fixture debe liberar el puerto; mantenerlo vivo debe conservarlo ocupado.

### F-02 — [CONFIRMADO] Alta — Una reparación lifecycle puede quedar bloqueada permanentemente en repairing

**Evidencia.** _repair_lifecycle_recovery_fault solo acepta repaired o armed en tools/dayz_mcp/loopback.py:2895-2899. Cambia armed → repairing en tools/dayz_mcp/loopback.py:2901-2909. Si la reparación falla intenta volver a armed, pero engulle cualquier excepción de esa transición en tools/dayz_mcp/loopback.py:2950-2959. La siguiente petición ve repairing y devuelve lifecycle_recovery_cas_conflict.

Una fixture controlada forzó fallo tanto en la reparación como en la transición de rearme:

- Primera llamada: HTTP 409, cleanup_failed.
- Estado persistido: repairing.
- Segunda llamada: HTTP 409, lifecycle_recovery_cas_conflict.

El camino vecino de reparación de auditoría sí admite reanudar repairing en tools/dayz_mcp/loopback.py:3006-3019, lo que confirma que hay un precedente local para recuperación idempotente.

**Mecanismo e impacto.** El estado durable se adelanta al trabajo, pero no existe recuperación segura cuando también falla la compensación. El except: pass elimina la evidencia operativa inmediata y deja un estado que el mismo handler rehúsa procesar. Es una degradación alta de recuperación: puede exigir intervención manual sobre estado persistente. No se demostró corrupción de datos de juego.

**[DESIGN] Cambio propuesto.** Primera opción, sin cambiar formato persistente: aceptar repairing solo cuando fault_id, head y manifest coinciden exactamente, y reanudar idempotentemente desde esa cabeza. No engullir el fallo de rearme; devolver un error 503 específico y rate-limited con fault_id. Solo si el modelo actual no puede expresar el resultado, añadir un estado terminal retryable explícito.

**Compatibilidad persistente.** La solución preferida no cambia el schema: estados legacy armed/repairing/repaired siguen válidos y un rollback sigue leyéndolos. Si se añadiera un estado nuevo, la versión anterior no lo entendería; durante la ventana de rollback habría que dual-escribir un marcador compatible o impedir que la versión antigua abra ese estado, con backup del directorio runtime antes de migrar.

**Gate verificable.**

- Fallo de repair + fallo de rearme no debe volver irrecuperable la siguiente llamada.
- Reiniciar entre ambas llamadas debe producir el mismo veredicto que continuar en proceso.
- Un head diferente debe seguir fallando cerrado por CAS.
- Dos reparadores concurrentes no deben ejecutar dos veces una compensación no idempotente.

### F-03 — [CONFIRMADO] Alta — Crecimiento lineal de hilos antes de autenticar

**Evidencia.** Handler asigna un timeout de socket de 35 s en tools/dayz_mcp/loopback.py:2355-2363, pero la autenticación solo ocurre después de que BaseHTTPRequestHandler haya leído la request line y cabeceras; do_GET llama a _authorized en tools/dayz_mcp/loopback.py:2372-2376. ExclusiveThreadingHTTPServer hereda ThreadingHTTPServer sin límite de workers en tools/dayz_mcp/loopback.py:3103-3108.

En una instancia controlada:

- Baseline: 2 hilos.
- 24 conexiones TCP locales que no enviaron request: 26 hilos.
- Delta: exactamente 24 workers.

El timeout acota la duración individual, pero no la concurrencia. La defensa positiva de autenticación sí usa hmac.compare_digest en tools/dayz_mcp/loopback.py:2434-2444 y el body está limitado a 1 MiB en tools/dayz_mcp/loopback.py:150-154 y tools/dayz_mcp/loopback.py:2446-2462.

**Mecanismo e impacto.** Cada accept crea un worker antes de que exista clave para validar. Un proceso local sin credencial puede abrir conexiones más rápido de lo que expiran y consumir threads, memoria y scheduling. El bind es 127.0.0.1, por lo que no es una exposición remota; requiere acceso local. Es agotamiento local reproducible, no crash observado.

**[DESIGN] Cambio propuesto.** Añadir un límite duro de conexiones activas mediante pool fijo o semáforo adquirido antes de crear/servir el worker. Al alcanzar el límite, cerrar la conexión o devolver 503 si ya puede parsearse. Mantener el timeout como segunda defensa.

**Gate verificable.**

- Con N conexiones ociosas, N mayor que el cap, el número de workers no supera cap + hilos base.
- Una petición autenticada de status sigue respondiendo bajo el límite.
- Al expirar/cerrar conexiones se recuperan slots sin fuga.
- El shutdown no queda esperando workers bloqueados.

### F-04 — [CONFIRMADO] Media — El launcher nativo ignora la ruta relocada de DayZ Tools

**Evidencia.** El resolver soporta DAYZ_TOOLS_ROOT y layouts alternativos en tools/dayz_mcp/dayz_tools_paths.py:204-212 y tools/dayz_mcp/dayz_tools_paths.py:226-268. La prueba D:\SteamLibrary valida explícitamente AddonBuilder y DayZDiag relocados en tools/tests/test_dayz_tools_paths.py:83-107. El manifest incorpora external_files resueltos en tools/build_native_launcher.py:723-733 y native_bundle clasifica AddonBuilder contra esa ruta en tools/dayz_mcp/native_bundle.py:676-681.

Sin embargo, el ejecutable C++ vuelve a codificar la ruta histórica en dos autoridades independientes:

- Construcción del comando: tools/native-launchers/dayz-test-v1/src/launcher.cpp:1007-1023.
- manifest_path anunciado/verificado: tools/native-launchers/dayz-test-v1/src/launcher.cpp:1195-1205.

**Mecanismo e impacto.** Un bundle puede cerrar y verificar D:\...\AddonBuilder.exe como dependencia externa, pero el launcher intenta ejecutar C:\Program Files (x86)\Steam\...\AddonBuilder.exe. En instalaciones no estándar, el descriptor y la ejecución divergen y el build falla aunque el preflight Python haya encontrado correctamente la herramienta.

**[DESIGN] Cambio propuesto.** Generar un header de build con la ruta resuelta de AddonBuilder, igual que ya se genera addon_roots.h en tools/build_native_launcher.py:1003-1010. El C++ debe consumir una única constante tanto para application como para manifest_path.

**Gate verificable.**

- Fixture sintética D:\ debe aparecer en manifest, descriptor y comando.
- La ruta C: por defecto no debe quedar como literal en launcher.cpp.
- Builds repetidos siguen siendo byte-idénticos.

### F-05 — [CONFIRMADO] Media — La metadata declara Python 3.10 aunque producción usa APIs 3.11+ y el instalador exige 3.14

**Evidencia.** tools/pyproject.toml:8 declara requires-python >=3.10. Producción importa tomllib sin fallback en tools/dayz_mcp/host_config.py:12 y datetime.UTC en tools/dayz_mcp/identity_migration.py:13; ambas decisiones excluyen 3.10. Invoke-Python solicita py -3.14 en tools/install-mcp.ps1:36-43. README.md:122-126 y QUICKSTART.md:10-12 reconocen expresamente la contradicción.

**Mecanismo e impacto.** pip puede aceptar el paquete en un intérprete que la aplicación no puede importar. Es una degradación de instalación y soporte, no un fallo solo documental.

**[DESIGN] Cambio propuesto.** Alinear metadata y política real. Si el único runtime validado y empaquetado es 3.14, declarar >=3.14,<3.15. Si se quiere soportar 3.11–3.14, declarar >=3.11 y añadir una matriz real; 3.10 exige compatibilidad adicional. No recomendaría mantener “instalable” una combinación sin pruebas.

**Gate verificable.**

- pip rechaza las versiones fuera de política antes de instalar.
- La versión mínima declarada importa todos los módulos y ejecuta la suite rastreada.
- README, QUICKSTART, pyproject e instalador contienen la misma política.

### F-06 — [CONFIRMADO] Media — Una auditoría todavía pendiente se reporta como audit_failed

**Evidencia.** RELEASE_AUDIT_TIMEOUT_S es 0,05 s en tools/dayz_mcp/session_coordination.py:40-43. La release fija _handoff_pending antes del I/O en tools/dayz_mcp/session_coordination.py:2253-2258 y arranca un worker en tools/dayz_mcp/session_coordination.py:2270-2280. Si el worker no termina dentro del wait, _write_release_audits_bounded_locked devuelve False, True en tools/dayz_mcp/session_coordination.py:2444-2465. El caller traduce cualquier not audit_ok a audit_failed en tools/dayz_mcp/session_coordination.py:2281-2284. El worker puede terminar correctamente después y limpiar el handoff en tools/dayz_mcp/session_coordination.py:2423-2442. release devuelve igualmente released=true con cleanup_degraded en tools/dayz_mcp/session_coordination.py:1125-1153.

Con un audit sink deliberadamente lento, release volvió aproximadamente a los 0,0616 s con released=true + audit_failed; handoff_pending era true inmediatamente y pasó a false al terminar el worker.

**Mecanismo e impacto.** Se colapsan tres estados distintos —pendiente, fallo al arrancar y fallo terminal— en el mismo texto audit_failed. Esto genera telemetría falsa, respuestas ambiguas y tests con carreras. El fallo de tools/tests/test_bug046_audit_fault_recovery.py:1630-1643 apareció en la suite completa y desapareció al aislarlo.

**[DESIGN] Cambio propuesto.** Modelar audit_pending separadamente. Solo marcar audit_failed cuando el worker no arranca o termina con fallo. Si la API necesita respuesta terminal, esperar dentro del presupuesto completo de cleanup; si prioriza latencia, devolver 202/pending o released=true + audit_pending con un estado consultable.

**Gate verificable.**

- Sink lento que finalmente tiene éxito nunca aparece como audit_failed.
- Sink que devuelve false sí produce fallo terminal.
- Fallo de worker.start es distinto de timeout de espera.
- Los tests esperan un evento/condición explícita, no una lectura inmediata dependiente del scheduler.

### F-07 — [CONFIRMADO] Baja — test_bug046_startup_deadlock contiene una carrera de observación

**Evidencia.** La fixture lanza un hijo corto y recorre daemons globales cada 20 ms; después exige haber visto el PID en tools/tests/test_bug046_startup_deadlock.py:753-781. En la suite completa no lo observó. En tres ejecuciones aisladas pasó.

**Mecanismo e impacto.** El hijo puede terminar entre muestras y la aserción se evalúa antes de usar stderr como diagnóstico. Es una degradación del harness: añade ruido rojo y puede ocultar regresiones reales entre reruns. No demuestra un deadlock actual del producto.

**[DESIGN] Cambio propuesto.** Hacer que el hijo publique readiness por pipe/evento propio y conservar su process handle. Recoger stdout/stderr antes de afirmar. Evitar descubrir la fixture mediante un scan global de procesos no pertenecientes al test.

### F-08 — [CONFIRMADO] Media — Los controles mutantes de vehículo no comprueban asiento de conductor ni ownership

**Evidencia.** ResolveOwnedCar obtiene jugador, HumanCommandVehicle y Transport, pero no llama GetVehicleSeat ni IsOwner en addon/scripts/5_Mission/MCPClientBridge.c:2133-2148. EngineSet lo usa y responde ok tras EngineStart/Stop en addon/scripts/5_Mission/MCPClientBridge.c:786-819. VehicleControl hace lo mismo en addon/scripts/5_Mission/MCPClientBridge.c:822-891. Telemetry también usa ese resolver en addon/scripts/5_Mission/MCPClientBridge.c:894-929.

El propio trace implementa el contrato más estricto: exige VEHICLESEAT_DRIVER e IsOwner en addon/scripts/5_Mission/MCPClientBridge.c:955-991. Las APIs existen con las firmas exactas GetVehicleSeat() en P:\scripts\3_Game\human.c:694-697 e IsOwner() en P:\scripts\3_Game\entities\pawn.c:191-194. Las tools se describen como “owned” y “owner-side” en tools/dayz_mcp/server.py:3410-3458.

**Mecanismo e impacto.** El resolver cuyo nombre promete ownership solo demuestra “sentado en un CarScript”. Un pasajero o peer no owner puede superar la validación y recibir ok. Está confirmada la violación del contrato y el falso positivo de validación; el efecto físico exacto de cada setter bajo autoridad de red requiere prueba in-game.

**[DESIGN] Cambio propuesto.** Crear una única resolución fail-closed para mutaciones: no_player, not_seated, not_driver, no_vehicle, not_owner. Reutilizarla en engine_set y vehicle_control. Para telemetry, decidir explícitamente si es lectura de cualquier ocupante o lectura owner-only y renombrar/documentar acorde.

**Gate verificable.**

- Conductor owner: control aceptado.
- Pasajero: not_driver.
- Conductor sin ownership: not_owner.
- No sentado: not_seated.
- Ninguno de los rechazos modifica engine/control y ninguno responde ok.

### F-09 — [CONFIRMADO] Media — vehicle_control incorpora un autopiloto de debug que contradice el verbo granular

**Evidencia.** La tool promete controles sostenidos throttle/steer/brake/handbrake en tools/dayz_mcp/server.py:3422-3453 y existe una tool separada engine_set en tools/dayz_mcp/server.py:3410-3420. Sin embargo, CarScript.OnInput:

- Autoarranca o detiene el motor y marca engineReady=false si RPM está bajo idle: addon/scripts/4_World/MCP_CarScript.c:616-632.
- Solo aplica todos los controles, incluidos brake y handbrake, cuando engineReady es true: addon/scripts/4_World/MCP_CarScript.c:634-660.
- Cambia marchas manuales implícitamente: addon/scripts/4_World/MCP_CarScript.c:638-653.

Ese bloque declara que “espeja autopiloto” y procede del código vanilla de diagnóstico encerrado en DIAG_DEVELOPER en P:\scripts\4_World\entities\vehicles\carscript.c:1303-1382. El original incluso comenta que el control básico “doesn't actually work” en P:\scripts\4_World\entities\vehicles\carscript.c:1347-1351. Las firmas nativas de setters están verificadas en P:\scripts\3_Game\vehicles\car.c:192-223 y motor en P:\scripts\3_Game\vehicles\car.c:237-247.

Además, product-spec.md:111-116 exige un gear_shift granular, y product-spec.md:293-300 vuelve a enumerarlo, pero no existe implementación rastreada con ese nombre.

**Mecanismo e impacto.** Un comando de frenado puede no aplicar freno durante la fase engineReady=false. Un comando genérico también puede arrancar motor y cambiar marcha sin que el caller lo haya pedido. Esto es comportamiento de fuente confirmado y una desviación del contrato; el impacto cinemático exacto no se validó in-game.

**[DESIGN] Cambio propuesto.** Simplificar la primitiva: vehicle_control reaplica incondicionalmente los cuatro setters mientras vive el TTL; engine_set es la única autoridad de motor. Si G1 sigue exigiendo gear_shift, implementarlo como verbo explícito; si no, retirarlo primero de la especificación mediante decisión de producto. No copiar lógica de autopiloto DIAG en una primitiva de control.

**Gate verificable.**

- Brake/handbrake se aplican aunque el motor esté apagado.
- vehicle_control no cambia engine state ni gear por sí solo.
- engine_set no cambia throttle/steer/brake.
- TTL y vehicle_release dejan los cuatro valores en neutral.
- Prueba owner-client con readback <=0,001 y caso adversarial de pasajero/no owner.

### F-10 — [CONFIRMADO] Media — El keyfile se lee con dos políticas de seguridad incompatibles

**Evidencia.** El lector simple abre, lee sin límite y sigue la resolución normal del filesystem en tools/dayz_mcp/loopback.py:3095-3100. El daemon lo importa en tools/dayz_mcp/daemon.py:40-45 y lo usa en tools/dayz_mcp/daemon.py:809-813. El runtime embebido también lo usa en tools/dayz_mcp/server.py:537-544.

En cambio, read_pinned_keyfile canonicaliza, rechaza reparse parents, abre con OPEN_REPARSE_POINT, exige disco, un solo hardlink, tamaño acotado y coincidencia de ruta final en tools/dayz_mcp/pinned_keyfile.py:122-175.

**Mecanismo e impacto.** La misma credencial tiene una frontera endurecida en caminos administrativos y otra más débil en el arranque del servicio que la consume. Existe superficie local de reparse/TOCTOU/archivo sobredimensionado. No se reprodujo exfiltración ni bypass; la explotabilidad depende de quién puede reemplazar la ruta configurada.

**[DESIGN] Cambio propuesto.** Hacer read_pinned_keyfile la única entrada de credenciales en daemon y embedded runtime. Eliminar read_key o dejarlo como wrapper exacto del lector endurecido.

**Gate verificable.**

- Fichero normal válido inicia.
- Vacío, BOM, oversize, symlink/reparse, múltiples hardlinks y path final divergente fallan cerrados.
- Los errores no imprimen la clave.

### F-11 — [CONFIRMADO] Baja — Los resultados completados pueden retenerse indefinidamente

**Evidencia.** ServerState crea _results sin política de tamaño en tools/dayz_mcp/loopback.py:744-748 y almacena cada respuesta terminada en tools/dayz_mcp/loopback.py:2057-2065. Solo se elimina cuando /await usa remove=1, tools/dayz_mcp/loopback.py:2088-2112. El handler mantiene remove=0 por compatibilidad con el harness en tools/dayz_mcp/loopback.py:2765-2781.

Client.await_result sí consume cuando tiene identity + lease en tools/mcp_client.py:285-297. Pero await_many consulta /await sin remove en tools/mcp_client.py:660-678 y se usa en las ráfagas de phase2 en tools/mcp_client.py:1339-1408. enqueue_cmd_status tampoco fija operation_timeout_s, tools/mcp_client.py:250-273.

Una fixture almacenó un resultado, lo leyó dos veces sin remove y confirmó results_pending=1 sin deadline.

**Mecanismo e impacto.** El TTL de comandos pendientes no es una TTL de resultados completados. Harnesses legacy o clientes autenticados que no consumen pueden hacer crecer memoria a lo largo de la vida del daemon. Es degradación de memoria local; no se observó agotamiento.

**[DESIGN] Cambio propuesto.** Primero, sin tocar el contrato legacy, hacer que await_many consuma con remove=1. Añadir después una TTL/LRU acotada para resultados completados no consumidos, con métrica de evicción. Mantener lectura no destructiva solo dentro de una ventana corta documentada.

### F-12 — [CONFIRMADO] Baja — El schema de comandos está fragmentado y algunos verbos aceptan campos desconocidos

**Evidencia.** Hay conjuntos separados SERVER_COMMANDS, CLIENT_COMMANDS y 18 _SCHEMALESS_COMMANDS en tools/dayz_mcp/loopback.py:46-116. El comentario confía en la validación de server.py, pero el loopback es también un ingress autenticado directo; los verbos schemaless retornan true sin inspeccionar args en tools/dayz_mcp/loopback.py:644-650.

Incluso entre ramas validadas, object_delete comprueba object_id pero no el conjunto exacto de claves en tools/dayz_mcp/loopback.py:460-466, y notify_players acepta extras en tools/dayz_mcp/loopback.py:468-480. En una prueba directa ambos aceptaron unexpected="x"; vehicle_control aceptó un dict arbitrario por ser schemaless.

**Mecanismo e impacto.** La whitelist, peer, mutabilidad, lease y schema viven en autoridades diferentes. Un caller con la clave que hable directamente al loopback evita los wrappers FastMCP. El bridge puede volver a rechazar o ignorar los extras, por lo que no se confirmó una mutación indebida; sí está confirmada la política no fail-closed.

**[DESIGN] Cambio propuesto.** Una registry Python única CommandSpec debe definir peer, read-only/mutante, lease, claves exactas, tipos y rangos. server.py y loopback deben consumirla. No generaría Enforce desde esa registry en el primer cambio: aumentaría el blast radius antes de estabilizar el contrato.

### F-13 — [CONFIRMADO] Media — El árbol de trabajo no es reproducible desde HEAD

**Evidencia.** git status --porcelain --untracked-files=all devolvió 19.689 ficheros no rastreados frente a 259 rastreados. Los mayores grupos eran _fase3 (8.238), _s0 (4.797), _fase2 (2.757), _poc (1.681), _fase1 (369), tools (344), reviews (306) y _backups (255).

Nueve tests con nombres activos viven directamente bajo tools/tests pero no están versionados:

- test_h8_distributed_gate.py
- test_process_job_spike.py
- test_session_e2e.py
- test_task7_final_authority_regressions.py
- test_task7_final_lifecycle_regressions.py
- test_task7_rereview_regressions.py
- test_task7_review_regressions.py
- test_task9_build_a_smoke.py
- test_task9_protocol_docs.py

La suite actual ejecuta 2.090 tests; la suite reproducible desde los módulos rastreados ejecuta 1.754. No hay workflows .github rastreados.

**Mecanismo e impacto.** Un fresh clone no puede reproducir los gates que ve el desarrollador y los artefactos masivos dificultan distinguir producto, evidencia y residuos. Es riesgo de release/proceso, no un defecto runtime. README.md:228-230 promete que un fresh clone solo estará rojo por regresiones salvo un flake conocido, pero el snapshot rastreado tiene además F-01.

**[DESIGN] Cambio propuesto.** Clasificar los nueve tests: versionar los que sean gates vigentes y mover experimentos/artefactos a una raíz externa o ignorada explícitamente. Añadir CI Windows para suite rastreada y audits puros; mantener PACKONLY/in-game como gates separados por necesitar DayZ Tools.

### F-14 — [CONFIRMADO] Baja — El bootstrap Python no está completamente fijado

**Evidencia.** Las dependencias runtime sí están fijadas exactamente en tools/pyproject.toml:11-15. En cambio, el frontend de build permite cualquier setuptools >=64 en tools/pyproject.toml:1-3 y el instalador actualiza pip a latest antes de instalar en tools/install-mcp.ps1:324-327.

**Mecanismo e impacto.** Dos instalaciones con la misma revisión pueden resolver toolchains de packaging distintas. El builder nativo sí contiene controles más fuertes: genera manifest/contrato y verifica el bundle en tools/build_native_launcher.py:995-1011; opcionalmente compara tres builds reproducibles en tools/build_native_launcher.py:1081-1102. La inconsistencia queda en el bootstrap Python.

**[DESIGN] Cambio propuesto.** Fijar pip, setuptools y wheel en un lock/bootstrap versionado; idealmente añadir hashes. Mantener separadas las dependencias runtime de las de build. Esto es un riesgo de deriva, no una vulnerabilidad de supply chain demostrada.

## Hallazgos potenciales o no confirmados

### P-01 — [POTENCIAL / NO CONFIRMADO] La validación URL Enforce es solo lexical

MCPBridge acepta cualquier string con prefijo http://127.0.0.1: en addon/scripts/5_Mission/MCPBridge.c:174-193. MCPClientBridge hace lo mismo en addon/scripts/5_Mission/MCPClientBridge.c:293-312. Strings con userinfo, sufijos o sintaxis ambigua podrían superar el prefijo y ser interpretados de otra forma por RestContext.

No se verificó el parser cerrado de RestContext, así que no afirmo SSRF ni exfiltración. **[DESIGN]** Validar una gramática exacta: scheme http, host literal 127.0.0.1, puerto decimal 1..65535 y, como máximo, slash final. Añadir fixtures dentro del runtime DayZ antes de asignar severidad de seguridad.

### P-02 — [POTENCIAL / NO CONFIRMADO] pollHz no tiene finitud ni techo

Ambos bridges aceptan cfg.pollHz cuando es >0 y lo copian directamente: addon/scripts/5_Mission/MCPBridge.c:188-190 y addon/scripts/5_Mission/MCPClientBridge.c:307-309. No se vio check IsFiniteFloat ni máximo. Un valor enorme podría convertir el poll en trabajo por frame; NaN/Inf dependen del parser/config.

**[DESIGN]** Acotar a un rango explícito y finito compartido por servidor/cliente. Validar frecuencia efectiva y consumo in-game.

### P-03 — [POTENCIAL / NO CONFIRMADO] El Windows SDK también tiene una raíz fija

El lock suministra versión y paths de cl/link, pero _compile fija C:\Program Files (x86)\Windows Kits\10 en tools/build_native_launcher.py:835-860. Puede fallar en una instalación SDK relocada aunque el MSVC lock sea correcto. No había un segundo entorno con SDK alternativo para reproducirlo.

**[DESIGN]** Resolver y fijar paths exactos del SDK en el lock, o descubrirlos una vez y grabarlos en el receipt; no mezclar versión fijada con raíz implícita.

### P-04 — [POTENCIAL / NO CONFIRMADO] El estado SetBrakesActivateWithoutDriver(false) no se restaura explícitamente

MCPCarDrive.Clear solo borra active y car en addon/scripts/4_World/MCP_CarScript.c:22-26, mientras OnInput llama SetBrakesActivateWithoutDriver(false) en addon/scripts/4_World/MCP_CarScript.c:655-660. Vanilla también usa false en su bloque de debug, P:\scripts\4_World\entities\vehicles\carscript.c:1377-1381, por lo que puede ser el default correcto. No se verificó si es estado persistente ni su efecto tras TTL/release.

**[DESIGN]** Leer o aislar el default real in-game antes de cambiarlo. Si persiste, restaurar exactamente el valor anterior, no asumir true.

### P-05 — [POTENCIAL / NO CONFIRMADO] Falta verificación de compilación y comportamiento Enforce

La inspección estática confirmó capas, firmas y ausencia de varios anti-patrones, pero el addon no estaba materializado en el sparse worktree y no existe linter offline local. Por ello quedan sin confirmar:

- Compilación real del snapshot HEAD con AddonBuilder.
- Autoridad efectiva de EngineStart/SetBrake desde pasajero/no owner.
- Semántica del parser RestContext.
- Readback y física de los controles tras simplificar F-09.
- Efecto de SetBrakesActivateWithoutDriver tras release.

Esto es una limitación explícita, no un hallazgo de defecto.

## Sobrecomplejidad y sobreingeniería

### O-01 — build_app es un composition root de 1.488 líneas

tools/dayz_mcp/server.py:2332-3819 contiene build_app con 1.488 líneas y aproximadamente 260 nodos de control en el análisis AST. Registra decenas de tools, validación, locks, wrappers y documentación en una sola función. El módulo completo tiene 3.894 líneas físicas.

**Coste.** Revisiones con blast radius enorme, navegación difícil, conflictos frecuentes y tentación de probar implementación mediante introspección.

**[DESIGN] Simplificación.** Mantener build_app como composition root pequeño y extraer registradores por dominio: sesión/lifecycle, mundo, vehículo, UI y artefactos. Cada registrador recibe app + runtime y no crea una segunda arquitectura. No cambiar nombres ni wire contract durante la extracción.

### O-02 — Se modifica una API privada de FastMCP para exponer el argumento from

_patch_public_argument_alias accede a app._tool_manager, muta parameters y reemplaza fn_metadata.call_fn_with_arg_validation en tools/dayz_mcp/server.py:1461-1479. Se aplica una sola vez a scene_raycast en tools/dayz_mcp/server.py:3818.

**Coste.** El pin de mcp reduce deriva hoy, pero cualquier actualización interna puede romper el parche sin error de tipos. El mecanismo es desproporcionado para un alias.

**[DESIGN] Simplificación.** Preferir un nombre público estable no reservado como from_pos/origin, o una capacidad pública documentada de schema/alias si la versión fijada la ofrece. Esa API externa debe verificarse antes de implementar; no se inventa aquí una firma.

### O-03 — MCPArgs y MCPResult son DTO universales para todos los verbos

MCPArgs acumula campos de todos los comandos en addon/scripts/5_Mission/MCPMessages.c:43-150. MCPResult hace lo mismo en addon/scripts/5_Mission/MCPMessages.c:410-472. La consecuencia ya requirió result_prune.py, que documenta la ambigüedad entre “vacío real” y “campo nunca rellenado” en tools/dayz_mcp/result_prune.py:1-22 y mantiene listas manuales de excepciones en tools/dayz_mcp/result_prune.py:29-56. Su comentario de línea 3 todavía cita posiciones antiguas de MCPResult, una señal menor de deriva.

**Coste.** Cada verbo amplía tipos globales, serializa ruido y obliga a políticas semánticas laterales. El consumidor no puede distinguir ausencia de cero/false/"".

**[DESIGN] Simplificación sin formato nuevo.** Primero centralizar el schema Python y un normalizador explícito por comando; conservar el wire actual. Eso resuelve duplicación y hace auditable la semántica sin migración.

**[DESIGN] Alternativa futura con cambio de formato.** DTOs por dominio o payload result tipado serían más limpios, pero requieren versión nueva. Legacy: el lector nuevo debe aceptar v8 plano. Transición: emitir envelope nuevo manteniendo el payload v8 hasta cerrar la ventana. Rollback: la versión anterior debe seguir encontrando v8; si se deja de emitir, el rollback no es seguro. No es una migración destructiva y no debe preceder a F-01/F-03.

### O-04 — Los bridges servidor y cliente duplican una capa de transporte casi completa

MCPBridge.c tiene 3.492 líneas y MCPClientBridge.c 3.444. Hay implementaciones paralelas de:

| Helper | Servidor | Cliente |
|---|---:|---:|
| DrainPending | addon/scripts/5_Mission/MCPBridge.c:298 | addon/scripts/5_Mission/MCPClientBridge.c:492 |
| ReloadKeyAfterFailure | addon/scripts/5_Mission/MCPBridge.c:389 | addon/scripts/5_Mission/MCPClientBridge.c:462 |
| EncodeQueryValue | addon/scripts/5_Mission/MCPBridge.c:2007 | addon/scripts/5_Mission/MCPClientBridge.c:3151 |
| VectorToArray | addon/scripts/5_Mission/MCPBridge.c:2402 | addon/scripts/5_Mission/MCPClientBridge.c:3038 |
| StringHasPrefix | addon/scripts/5_Mission/MCPBridge.c:2410 | addon/scripts/5_Mission/MCPClientBridge.c:3108 |
| EncodeNetworkMoveStrategy | addon/scripts/5_Mission/MCPBridge.c:3134 | addon/scripts/5_Mission/MCPClientBridge.c:2547 |
| PostResult | addon/scripts/5_Mission/MCPBridge.c:3369 | addon/scripts/5_Mission/MCPClientBridge.c:3294 |
| ShutdownInstance | addon/scripts/5_Mission/MCPBridge.c:3479 | addon/scripts/5_Mission/MCPClientBridge.c:200 |

**Coste.** Fixes de seguridad/configuración pueden aplicarse a un lado y olvidarse en el otro; P-01 y P-02 ya muestran el doble mantenimiento.

**[DESIGN] Simplificación.** Extraer un componente pequeño y común para config, URL, polling/backoff, key reload, serialización/post y drenaje. Mantener dispatch y lógica de dominio separados. Evitar una superclase que mezcle MissionServer y MissionGameplay: sería sustituir duplicación por herencia frágil.

### O-05 — Hay varias autoridades Win32 para la misma noción de identidad de path/fichero

Existe win32_fileinfo.py con bindings comunes, usado por pinned_keyfile y request_path_authority. Aun así se repiten CreateFileW/GetFinalPathNameByHandleW o comparadores de path en:

- tools/build_native_launcher.py:95-120 y tools/build_native_launcher.py:319-344.
- tools/dayz_mcp/host_config.py:475-484 y tools/dayz_mcp/host_config.py:629-676.
- tools/dayz_mcp/identity_migration.py:53-62 y tools/dayz_mcp/identity_migration.py:285.
- tools/dayz_mcp/launcher_registry.py:36-45 y tools/dayz_mcp/launcher_registry.py:456.
- tools/dayz_mcp/doctor.py:318.
- tools/dayz_mcp/native_process_snapshot.py:59-64.
- tools/dayz_mcp/request_path_authority.py:130.

**Coste.** No es solo estética: F-01 es un defecto real provocado por una igualdad demasiado débil en una de esas autoridades.

**[DESIGN] Simplificación.** Una librería win32_identity debe proporcionar bindings, canonical path por handle, file ID y same_file. Los módulos de política siguen decidiendo qué aliases/reparse/hardlinks permiten; no se debe concentrar toda la política de seguridad en un helper genérico.

### O-06 — SessionCoordinator concentra demasiados estados y efectos

tools/dayz_mcp/session_coordination.py tiene 3.735 líneas. acquire ocupa 431 líneas desde tools/dayz_mcp/session_coordination.py:265 y mezcla FIFO, expiración, persistencia, audit, WAL, fault latches y cleanup. La complejidad es parcialmente inherente al problema, pero F-02 y F-06 muestran que distinguir estado pendiente, terminal y compensado ya resulta difícil.

**[DESIGN] Simplificación.** No hacer una reescritura global. Primero caracterizar transiciones con tablas y tests; después extraer un reducer puro de estado y adaptadores separados para persistencia/audit. Los efectos se ejecutan después de decidir la transición y el resultado vuelve a alimentar el reducer. Preservar el formato durable inicialmente.

### O-07 — validate_command_args es una cadena de 389 líneas

tools/dayz_mcp/loopback.py:262-650 contiene una función de 389 líneas y aproximadamente 178 nodos de control. Convive con listas de comandos y validadores de server.py/Enforce.

**Coste.** Añadir un verbo exige sincronizar múltiples sitios; las omisiones se convierten en _SCHEMALESS_COMMANDS. F-12 es el resultado observable.

**[DESIGN] Simplificación.** Reemplazar ramas por datos declarativos CommandSpec más validadores pequeños solo para estructuras complejas. La registry debe rechazar claves extra por defecto.

### O-08 — Parte de la suite inspecciona la forma del source en vez del contrato

El ejemplo más claro deriva por AST el techo de timeout leyendo inspect.getsource(Handler._handle_session) en tools/tests/test_handler_socket_timeout.py:67-93. El valor real está incrustado en la rama /wait de tools/dayz_mcp/loopback.py:2558-2564. En el conjunto rastreado se localizaron 37 usos de ast.parse/inspect.getsource en 22 ficheros y 53 usos de time.sleep/asyncio.sleep en 19 ficheros.

**Coste.** Refactors semánticamente neutros rompen tests, y sleeps introducen carreras como F-06/F-07.

**[DESIGN] Simplificación.** Elevar límites compartidos a constantes públicas internas y probar comportamiento en la frontera. Reemplazar sleeps por eventos, condiciones o relojes inyectados. Mantener tests de source solo para invariantes de seguridad que realmente deban prohibir una construcción.

## Controles positivos que conviene conservar

- **Superficie local deliberada.** La documentación declara bind 127.0.0.1 y ausencia de modo remoto en README.md:127-130. La auditoría HTTP no encontró nuevos clientes/redes fuera de la allowlist.
- **Autenticación y tamaño.** compare_digest y límite de body están correctamente presentes en tools/dayz_mcp/loopback.py:2434-2462.
- **Fail-closed de peer/whitelist.** El ingress rechaza comando no whitelisted, args no dict y peer incorrecto en tools/dayz_mcp/loopback.py:1336-1348.
- **Dependencias runtime fijadas.** mcp, Pillow y psutil están exactos en tools/pyproject.toml:11-15.
- **Build nativo con evidencia.** Se emiten build-contract, closure-manifest y headers, y se verifica el bundle en tools/build_native_launcher.py:995-1011. El modo reproducible compara tres adquisiciones/builds en tools/build_native_launcher.py:1081-1102.
- **Separación de owner en trace.** El camino vehicle_trace contiene la comprobación correcta de driver + owner en addon/scripts/5_Mission/MCPClientBridge.c:955-991; debe convertirse en el patrón compartido.
- **API DayZ verificada.** Los overrides auditados coinciden con vanilla y no aparecieron los anti-patrones mecánicos principales del checklist.
- **Cobertura amplia.** 1.754 tests rastreados y 2.090 en el árbol actual son una base valiosa; el problema principal es su reproducibilidad y sincronización, no ausencia de tests.

## Orden recomendado de remediación

| Orden | Acción | Hallazgos | Criterio de salida |
|---:|---|---|---|
| 1 | Unificar identidad Win32 y reparar watchdog | F-01, O-05 | Suite rastreada verde; alias P:/C: cubierto |
| 2 | Hacer lifecycle repairing reanudable | F-02 | Fallo doble + restart recuperables, CAS sigue cerrado |
| 3 | Acotar workers HTTP pre-auth | F-03 | Stress local mantiene techo de hilos |
| 4 | Propagar AddonBuilder relocado al C++ | F-04 | D:\ coherente en manifest, descriptor y comando |
| 5 | Endurecer owner/driver y simplificar controles | F-08, F-09, P-04 | Gates in-game owner/passenger/non-owner; freno con motor off |
| 6 | Separar audit_pending de audit_failed | F-06, F-07 | Tests sin sleeps/races y veredictos terminales precisos |
| 7 | Unificar keyfile y política Python | F-05, F-10, F-14 | Un solo lector; install matrix y bootstrap fijados |
| 8 | Acotar resultados y centralizar schemas | F-11, F-12, O-07 | Memoria acotada; extras rechazados |
| 9 | Sanear reproducibilidad del árbol y CI | F-13 | Fresh clone ejecuta los mismos gates rastreados |
| 10 | Refactors de complejidad con caracterización | O-01 a O-08 | Sin cambio de contrato; módulos y tests conductuales |

No recomendaría iniciar por la partición de server.py o por un wire v9: son mejoras reales, pero tienen más superficie y menos urgencia que los defectos reproducibles.

## Leads descartados o rebajados

- Los dos fallos intermitentes de la suite completa no se promocionaron automáticamente a defectos de producto. Uno se explica por estado pending observado demasiado pronto (F-06) y otro por una carrera del test (F-07).
- No se confirmó un deadlock general de startup en el snapshot actual.
- No se encontró un cliente HTTP runtime no autorizado por los audits existentes.
- No se encontraron secretos en los patrones examinados.
- No se encontraron errores mecánicos Enforce de los tipos buscados; esto no sustituye PACKONLY.
- No se afirmó que la validación URL permita SSRF porque el parser de RestContext no fue aislado.
- No se afirmó que passenger/no-owner mueva físicamente el coche; se confirmó que la validación y el ok no prueban el contrato.

## Limitaciones

1. No se lanzó DayZ ni se tomó lease MCP; la auditoría no gestionó procesos de juego.
2. No se compiló el addon por estar sparse-excluded y no existir el linter offline esperado.
3. No se construyó el launcher nativo ni se dispuso de instalaciones relocadas de DayZ Tools/Windows SDK para un E2E.
4. No se instalaron analizadores adicionales.
5. La enorme superficie no rastreada se inventarió, pero no se auditó fichero por fichero como producto porque no forma parte de HEAD y contiene artefactos históricos/generados.
6. Los hallazgos de runtime DayZ que dependen de autoridad, física o parser cerrado permanecen etiquetados como potenciales hasta un gate in-game.

## Veredicto final

El repositorio no está en estado “todo correcto”: HEAD tiene una regresión determinista en el watchdog y existen dos fallos de diseño reproducibles que pueden bloquear recuperación o agotar recursos locales. Tampoco está ante una crisis de corrupción o exposición remota demostrada. La arquitectura contiene controles sólidos, pero ha acumulado autoridades duplicadas y estados demasiado implícitos; los defectos más importantes aparecen justo en esas fronteras.

La estrategia más segura es reparar primero las tres invariantes operativas —vigilar al proceso correcto, poder reanudar toda reparación durable y acotar trabajo pre-auth—, luego corregir los contratos de build/vehículo, y solo después reducir la sobrecomplejidad mediante extracciones pequeñas con el wire y los formatos persistentes intactos.
