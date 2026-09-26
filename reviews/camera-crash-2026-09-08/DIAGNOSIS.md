# Diagnóstico Q1 — getter nativo identificado; corrección offline, motor pendiente

## Llamada que mata al proceso

**`Camera.GetCurrentCamera()` desreferencia un puntero nativo nulo antes de devolver una referencia al script.** No es `current.GetTransform()` ni `GetWorldPosition()` en las dos ocurrencias disponibles.

- `../SUB_BRZ_dev/_client/profiles/DayZDiag_x64_2026-09-08_14-05-19.RPT:475,479,683-689`: ENGINE Crashed, ACCESS_VIOLATION C0000005; frame más profundo `BuildCameraResult:3664`, seguido de `DispatchCameraGet:902`.
- `../SUB_BRZ_dev/_client/profiles/DayZDiag_x64_2026-09-08_14-58-42.RPT:479,483,687-693`: mismo código, dirección y pila.
- **Mapeo correcto:** `reviews/guards-2026-09-08/MCPClientBridge.c.BEFORE:3664` es `Camera current = Camera.GetCurrentCamera();`. `GetTransform` está en :3677 y `GetWorldPosition` en :3679, después de la llamada que no regresó. En la copia de esta lane, `MCPClientBridge.c.BEFORE:3683` es la llamada correspondiente; :3664 es ahora la cabecera del método. Mezclar versiones habría señalado el sitio incorrecto.
- `minidumps.json`, extraído por `inspect_minidumps.py`: los DOS minidumps contienen `RIP=0x7ff67c227910`, `RAX=0`, excepción de lectura de `0x68`; módulo `DayZDiag_x64.exe+0x4f7910`. Los bytes `48 8b 48 68` corresponden a `mov rcx,[rax+0x68]`. El RPT contiene los mismos bytes en :678 / :682. La correlación fuente/RPT identifica el getter; los registros confirman el acceso nulo dentro del nativo.

El parser solo lee cabecera, directorio, excepción, registros, módulo y bytes del código. No vuelca cadenas arbitrarias del heap ni modifica los artefactos. Layouts contrastados con Microsoft: [excepción](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_exception), [stream](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_exception_stream), [CONTEXT x64](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-context), [cabecera](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_header), [directorio](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_directory), [módulo](https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/ns-minidumpapiset-minidump_module).

## Por qué el guard existente no sirve aquí

`MCPClientBridge.c.BEFORE:676-678` define `IsClientInGame()` como `GetGame() && GetGame().GetPlayer()`. Eso prueba existencia del juego y del jugador, no la del objeto interno utilizado por el getter. Un jugador sentado satisface ambos predicados. El `if (!current)` posterior tampoco protege: el fallo ocurre **dentro** de la llamada que debería asignar `current`.

Vanilla distingue explícitamente la cámara del jugador de una entidad `Camera`: `../scripts/3_game/entities/camera.c:3-7` promete devolver null para la cámara del jugador. En estos dos estados el nativo no cumplió esa salida segura. `../scripts/4_world/entities/dayzplayerimplement.c:2849-2868` selecciona cámaras de vehículo según `HumanCommandVehicle`; las clases se registran en `../scripts/4_world/entities/manbase/dayzplayer/dayzplayercameras.c:18-19,60-61`.

**Límite causal:** los dumps no dan nombre/tipo al objeto interno nulo ni muestran quién lo retiró. Que la cámara del vehículo y/o `ReleaseCamera()` provoquen su ausencia es HIPÓTESIS. La hipótesis del brief de un `Camera` obsoleto devuelto al script y desreferenciado en `GetTransform()` queda refutada para estas dos pilas. Ambas ocurrencias pasaron por `camera_get`, no por el informe del job.

## Implementación realizada y límites

1. Se eliminó por completo `Camera.GetCurrentCamera()` del bridge: un getter que puede morir antes de devolver null no sirve como comprobación de disponibilidad. Ahora se utiliza la referencia `m_ActiveCam` que el propio bridge conserva, comprobando su presencia y `IsActive()` antes de leerla.
2. Rechazar estados sin jugador válido, con comando de vehículo o jugador con padre. El segundo indicador evita confiar exclusivamente en `GetCommand_Vehicle()` cuando la observación del brief ya advierte desacuerdo cliente/servidor. Un padre no se etiqueta automáticamente como vehículo. No se cambia la lógica de `vehicle_get_in_client`.
3. `CameraReadError()` retorna el nombre del estado. `BuildCameraResult` devuelve `camera.ok=false`, `viewport_moved=false`, error explícito y arrays vacíos antes de cualquier lectura de cámara en los estados rechazados. No se inventa `player_camera_active` por ausencia de una referencia MCP.
4. El estado rechazado también evita `Camera.IsInterpolationComplete()` en la fase SETTLE del job: pasa directamente a REPORT, que utiliza `BuildCameraResult` y produce el mismo fallo explícito. Las lecturas globales restantes de FOV/interpolación solo se mantienen con cámara MCP presente/activa y sin vehículo/padre. No se han demostrado responsables de estos crashes; la protección de SETTLE es prevención de lecturas globales en un estado no verificable, no atribución causal a ese getter.
5. Mantener el sobre `result.ok=true` y el resultado anidado de cámara, los dos llamantes, la versión/protocolo y todos los guards/ownership de hoy. No hay nuevos campos de mensajes ni del bridge, logs de tick ni cambios de daemon.

## Contrato vanilla abierto antes de usarlo

| API | Fuente y semántica verificada | Límite |
|---|---|---|
| `Camera.GetCurrentCamera()` | `../scripts/3_game/entities/camera.c:3-7`, `static proto native Camera`; devuelve la instancia activa, documenta null para cámara del jugador | Implementación C++ ausente; bug nativo evidenciado por los dumps |
| `GetCurrentFOV()` | Mismo archivo :9-13, getter **estático** de FOV actual | No garantiza seguridad sin cámara nativa; llamar mediante una instancia no lo convertiría en getter de esa instancia |
| `IsInterpolationComplete()` | Mismo archivo :26-29, estado de la interpolación actual; `InterpolateTo` :15-24 | No demuestra movimiento real del viewport |
| `Camera.IsActive()` | Mismo archivo :57-61, `proto native bool`, actividad de **esta instancia**. Uso vanilla con referencia comprobada en `../scripts/5_mission/gui/scriptconsolegeneraltab.c:553-556`; también :54 de `5_mission/gui/inventorynew/vicinityitemmanager.c` | No se ha ejecutado aquí contra el motor; los precedentes no prueban su implementación C++ |
| `GetTransform(out vector mat[])` | `../scripts/1_core/proto/enentity.c:274-288`, devuelve tantos vectores como tenga el array (1 a 4); aquí se pasan cuatro para la matriz completa | No demuestra que esa entidad sea el viewport efectivo. Corrección al primer tramo de este informe: no exige siempre cuatro |
| `GetWorldPosition()` | `../scripts/3_game/entities/object.c:295-297`, posición mundial teniendo en cuenta proxies | En las pilas, aún no ejecutado |
| `GetCurrentCameraPosition/Direction()` | `../scripts/3_game/global/game.c:729-731`, getters nativos vectoriales de CGame | No se utilizarán como fallback sin estado verificable |
| `GetCommand_Vehicle()` | `../scripts/3_game/human.c:1491-1494`, devuelve `HumanCommandVehicle`; uso vanilla en `dayzplayerimplement.c:2849` | Su ausencia no demuestra que el jugador no esté parentado |
| `GetParent()` | `../scripts/1_core/proto/enentity.c:570-571`, devuelve el padre IEntity | Un padre no prueba por sí solo el tipo vehículo |

La cámara hereda `Camera -> Entity -> ObjectTyped -> Object -> IEntity`; las declaraciones están en `camera.c:1`, `entity.c:1`, `objecttyped.c:1`, `object.c:64` y `enentity.c:164`. `m_ActiveCam` ya existe en la clase (`MCPClientBridge.c.BEFORE:206`), se asigna en :3307/:3351 y se limpia en :3984. `MCPCamera` declara todos los campos utilizados e inicializa los tres arrays en `addon/scripts/5_Mission/MCPMessages.c:283-300`. `PlayerBase -> ManBase -> DayZPlayerImplement -> DayZPlayer -> Human` está declarado respectivamente en `../scripts/4_world/entities/manbase/playerbase.c:49`, `4_world/entities/manbase.c:1`, `4_world/entities/dayzplayerimplement.c:86`, `3_game/dayzplayer.c:1158`, `3_game/human.c:1332`; así se alcanza la firma real de `GetCommand_Vehicle`. `CGame.GetPlayer()` devuelve `DayZPlayer` en `../scripts/3_game/global/game.c:946`; el cast existente a `PlayerBase` se comprueba antes de usarlo.

Anclas del resultado final (`addon/scripts/5_Mission/MCPClientBridge.c`): `DispatchCameraGet` :912-922; SETTLE :2608-2620; informe del job :3370-3379; disponibilidad :3675-3709; resultado de cámara :3711-3738. El bridge pasa de **94.919 a 95.702 bytes**, SHA-256 final `9ce3ec0bb1430dea4f91e15e5efa4fffb0d77d95570da259b8463ec4c5017a19`. La copia previa de esta lane tiene SHA-256 `e41bb9ea05cd214da44639ccc7ffb1e7186ae13c113b12994971004f63f7c47b`.

Mapa de datos: estado de jugador/parentado/cámara, solo CLIENTE; el servidor no lo proporciona. Transporte hacia Python: el resultado de cámara existente de `camera_get` o `camera_set`, sin cambios de formato.

## Efecto explícito sobre restore_gameplay

`tools/dayz_mcp/server.py:1850-1871` rechaza `camera.ok=false` como `unverified`; :4459-4485 consulta `camera_get` después de `restore_gameplay` y lo convierte en `restore_unverified`. `ReleaseCamera()` limpia `m_ActiveCam` (`MCPClientBridge.c.BEFORE:3968-3985`). Por ello, después de liberar la cámara **esta corrección degrada también la verificación en pie a restore_unverified**, con el motivo de cámara no disponible. Se prefiere esto a un crash o a declarar cámara del jugador observada por inferencia. Reintentar no recupera una observación que esta vía ya no realiza. Recuperar esa capacidad requiere una vía de lectura independiente validada en motor; no cambiar el consumidor para aceptar la ausencia como éxito.

El cleanup de simulación, controles, HUD y cámara se sigue ejecutando. No se certifican esas postcondiciones. Esta degradación es deliberada y material para revisión, no un detalle oculto tras tests verdes.

## Evidencia y validación final

- `red.log`: el mismo módulo nuevo contra `.BEFORE`, sin revertir el árbol, **Ran 12 tests in 0.031s / FAILED (failures=9) / exit 1**. El test que prohíbe `GetCurrentCamera()` usa la llamada identificada en las pilas como invariante independiente. Los otros fallos incluyen la ausencia de las ramas de disponibilidad y el fallback que afirmaba cámara del jugador. No son errores de importación ni del entorno.
- `green.log`: **21 módulos nombrados uno a uno, 327 tests, todos exit 0**. El nuevo módulo tiene 12 tests; cubre estructura de estados/orden de retornos, ambos llamantes y SETTLE. Sus comprobaciones del consumidor ejecutan el `restore_gameplay` real de Python con transporte simulado para los seis errores, y verifican `restore_unverified`. No ejecutan el bridge Enforce.
- `validator-before.json` y `validator-after.json`: **WARN, exit 2**, cero errores y los mismos dos avisos `ES-GETTYPE-EXACT-MATCH` de código ajeno al cambio (líneas 2026 y 2739 antes, 2026 y 2746 después). No se corrigieron por estar fuera del alcance de cámara.
- `validator-addon.json`: **FAIL, exit 1**, dos hallazgos en `MCPDialogController.c:39-40` por rutas de hot layout (`ES-LAYOUT-PATH-PBOPREFIX-MISMATCH`, `ES-LAYOUT-FILE-MISSING`), más cinco avisos de comparaciones `GetType`. El archivo de diálogo no se modificó. Son rutas de sondeo opcional con comprobación `FileExist` en :169; no se atribuyen al diff de cámara ni se afirma que representen un fallo de compilación. Resolver/ajustar esos gates está FUERA DE MI ALCANCE. El addon global no se declara verde.
- `MCP_BRIDGE_VERSION` sigue en `"10"`; no cambian `MCPMessages.c` ni módulos Python de producción. `tools/build_native_launcher.py:53-72` no contiene el bridge Enforce: este cambio no requiere resellado del launcher. La fuente aún necesita el despliegue coordinado por el receptor para que el juego la cargue.

## Qué está demostrado y qué no

| Dimensión | Estado |
|---|---|
| Mismo getter, dirección nativa y lectura nula en ambos runs | VERIFICADO en RPT, copia desplegada y dumps |
| Jugador sentado y cámara de vehículo sobreescribiendo la libre | Observación de la otra sesión proporcionada en el brief; no reproducida por esta lane |
| El objeto interno nulo es una cámara retirada por el vehículo | HIPÓTESIS; sin símbolos C++ ni evidencia de su ciclo de vida |
| Ambos consumidores de Build quedan protegidos por el mismo retorno temprano | VERIFICADO en fuente y tests estructurales |
| Enforce compila y la ejecución no crashea en los estados de interés | INCONCLUSO: no hay compilador y el brief prohíbe lanzar/tocar el juego |
| GetCurrentFOV/IsInterpolationComplete/IsActive no pueden fallar en ningún estado | No demostrado; no confundir la eliminación de la llamada observada con prueba de todos los nativos |
| Verificación de restore tras liberar cámara | Degradada deliberadamente a `restore_unverified`, también en pie; consumidor probado |

No se prueba aquí la paridad entre los scripts vanilla descomprimidos y el ejecutable 1.29.163709 de los dumps. No se reconstruye la cámara del vehículo ni se arregla `vehicle_get_in_client`. `camera_set` todavía puede aplicar sus cambios/suprimir controles antes de que el informe rechace la observación; `restore_gameplay` sigue ejecutando el cleanup, aunque su verificación ya no obtenga una lectura positiva. La lista concreta para la prueba conjunta y todos los comandos/salidas están en `STATE.md`.
