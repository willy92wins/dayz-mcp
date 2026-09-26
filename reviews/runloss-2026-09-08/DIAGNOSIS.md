# DIAGNOSIS - Q2 - 2026-09-08

Implementado y probado offline. El timeout prematuro es un aborto de un probe de 15 s mal etiquetado como vencimiento de la espera de 420 s. El motivo de retirada ya llegaba al bridge Python, pero se descartaba. Se preservan los codigos, el HTTP 409, los abortos y los reintentos existentes; se precisan los mensajes.

Resultado: 14 modulos nombrados, ultimas ejecuciones con exit 0; 587 pruebas contabilizadas, 586 ejecutadas y una omitida por privilegios de symlink. Las 15 regresiones nuevas pasan; contra las cuatro fuentes .BEFORE hay 13 fallos de comportamiento y dos controles que pasan. Ver STATE.md para comandos y resumen literal; red.log y green.log contienen stdout/stderr completos.

## 1. Por que wait_for devolvia antes

Citas del original, conservadas en este directorio:

- server.py.BEFORE:69 fija DEFAULT_TOOL_TIMEOUT_S en 15.0. :2658-2659 crea correctamente un deadline monotono para todo timeout_s.
- server.py.BEFORE:2721 limita CADA probe a min(15, max(remaining, POLL_INTERVAL_S)). :2723-2725 llama query_all_players en peer=server.
- ClientRuntime._await_result, server.py.BEFORE:1635-1672, espera el resultado del comando hasta su propio deadline. Si no llega, emite `timeout waiting for query_all_players id=...` y consulta una instantanea de frescura.
- server.py.BEFORE:2736-2742 captura ese texto y lanza inmediatamente `wait_for timed out waiting for ...`. No compara el reloj con el deadline global en esa rama. Ahi se confunde el presupuesto del probe con el de wait_for.
- server.py.BEFORE:217-222 y :2728-2735 SI reintentan los rechazos server_poll_stale y client_not_polling, tanto tokens simples como game_not_ready:reason=.... El texto `server peer last poll 140.4s ago` es un sufijo de diagnostico, no un rechazo server_poll_stale.

Experimento: ClientRuntime real, solo transporte _call y reloj inyectados; /enqueue acepta id=7, /await sigue pending hasta consumir exactamente los 15 s recibidos como presupuesto, /status informa la edad global 140.4. Sin daemon, socket ni juego. wait_for(timeout_s=420) sale a los 15 s con el texto antiguo. Esto reproduce el mecanismo del sintoma; no demuestra el instante exacto de la llamada historica ni que el proceso de produccion cargara estos mismos bytes.

Rojo literal del experimento:

```text
AssertionError: 'wait_for aborted' not found in 'wait_for timed out waiting for players_at_least; server peer last poll 140.4s ago; queue_depth=1; version_state=ok'
```

Cambio: tools/dayz_mcp/server.py:2740 comprueba el deadline al capturar el error. Si queda presupuesto, dice `wait_for aborted`; si se agoto, conserva `wait_for timed out`. Incluye reason=probe_timeout, elapsed_s, timeout_s y probe_timeout_s. La edad del poll lleva `station snapshot:`. Caso probado: 15 de 420 s aborta; 10 de 10 s vence. No se modifica el resultado normal satisfied/timed_out ni la politica de reintentos.

No se transforma un timeout de un comando ya aceptado en reintento automatico: lo prohibe la frontera de solo mensajes y podria dejar varios probes pendientes. ClientRuntime.abandon_bridge es deliberadamente no-op en server.py.BEFORE:1740-1743.

## 2. Donde se perdia la causa de retirada

- tools/dayz_mcp/process_lifecycle.py:3716 exige haber probado _run_all_dead. :3727 y :3744 registran all_processes_gone_or_foreign. :3736-3740 quitan dueno/procesos y ponen EXITED; :3748 informa al coordinador.
- tools/dayz_mcp/process_lifecycle.py:305-312 traduce run_reaped a la causa de binding `reaped`. :1295-1296 la pasa a bindings.retire_run. Esa llamada precede la persistencia: un fallo al persistir no autoriza a prometer EXITED.
- loopback.py.BEFORE:1119-1143 recibe reason, pero no lo utiliza; almacena None como valor de cada instancia retirada. :1132 descarta la cola con el unico codigo binding_retired.
- instance_fence.py.BEFORE:61-65 y :157-162 solo disponen de una receta comun para stop/reap/replacement.
- El caso de reemplazo no se ha inventado: tools/dayz_mcp/process_lifecycle.py:1204-1217 llama retire_role(..., "replace-role") antes de preparar la nueva instancia.

Cambio:

| Fuente actual | Comportamiento |
|---|---|
| tools/dayz_mcp/instance_fence.py:61 y :157 | Hints estaticos para reaped, replace-role y stopped; causa desconocida explicita, sin eco de texto arbitrario. No hay nuevos codigos ni enum cerrado de readiness. |
| tools/dayz_mcp/loopback.py:911 y :1120 | El anillo acotado existente conserva (rol, causa). La retirada adjunta hint a los resultados de comandos que ya estaban en cola. |
| tools/dayz_mcp/loopback.py:1327 | Un rechazo sin binding activo toma la ultima causa del mismo peer (offline cubre ambos). No toma la causa de un run anterior si hay un binding nuevo presente; si el anillo la expulso, no la inventa. |
| tools/dayz_mcp/loopback.py:2418 y :2458 | Propagacion opcional del hint al descartar la cola; se conservan id, ok, error y la contabilidad de operaciones. |
| tools/dayz_mcp/server.py:783 | La respuesta fallida de /await conserva el mismo hint acotado que /enqueue, con el limite ya existente de 240 caracteres. |

Reapeo: `binding_retired: retirement_reason=reaped; reason=all_processes_gone_or_foreign: The target run has no live owned process. Relaunch via dayz_test_run; rebinding cannot restore processes that are gone or foreign.`

Reemplazo: identifica replace-role y dirige a esperar la instancia nueva. Si la preparacion falla y no aparece una sucesora, dirige a lifecycle_status; ese caso tambien se reproduce con _prepare_instance y un perfil ausente.

Stop: identifica la ruta de cierre, sin afirmar que ya termino. Hallazgo adicional: tools/dayz_mcp/process_lifecycle.py:2963 y :3031 retiran con `stopped` cuando el run puede quedar UNRECONCILED o apenas pasar a STOPPING. Por eso el texto pide comprobar la finalizacion.

No se modifica process_lifecycle.py: su hash sigue siendo el de .BEFORE. El mensaje de reapeo aprovecha su discriminador existente, no ejecuta deteccion nueva ni consulta lifecycle sosteniendo el lock del bridge. El lock local usado para consultar tombstones es RLock (tools/dayz_mcp/loopback.py:889).

## 3. Sujeto de la espera y aviso al dueno

Emparejamiento exacto con el run adoptado: NO implementado por la frontera de contrato. ClientRuntime.session_acquire_wait (server.py.BEFORE:1319-1331) delega y devuelve el resultado, sin guardar el run para la espera. call_bridge (:1586-1627) manda peer/comando y recibe solo id. wait_for no acepta run_id. tools/dayz_mcp/core.py:107-118 proyecta el prefijo de instancia y edades, sin run_id. El timeout no lleva la identidad del run del comando. Con esto no puedo acreditar que una edad del peer pertenezca al run adoptado sin ampliar routing/estado/protocolo.

La lectura rancia tambien tiene mecanismo: loopback.py.BEFORE:2907 usa _peer_last_class cuando no hay candidato y :2910 usa _bound_last_poll_at[peer], no un reloj por instancia. Retirar no borra esas observaciones; confirmar otra instancia tampoco las convierte en polls de esta. BOUND no demuestra que el proceso siga vivo. El mensaje actualizado llama a esa evidencia station snapshot. Corregir bridge_status o enrutar por run_id queda FUERA DE MI ALCANCE por la restriccion de solo mensajes, incluso aunque loopback.py sea escribible.

El aviso opcional aprovecha /await para los comandos que estaban en cola al reapear. No se crea un canal push para duenos sin peticiones pendientes. Matiz al brief: tools/dayz_mcp/session_coordination.py:1907-1923 ya incrementa revision/despierta esperas y audita run_reaped_wake; eso no entrega al dueno una explicacion del run perdido. Cambiarlo requiere diseno/estado nuevos y tocar fuentes fuera de la lista.

## 4. Regresiones y limites

- tools/tests/test_runloss_diagnostics.py:56 y :66: reaper/lifecycle reales con guard simulado; lease local activo conservado; enqueue y /await muestran ausencia de procesos propios. terminate esta prohibido por el fixture.
- :72, :80, :89, :93 y :99: reemplazo, sucesora que no puede prepararse, cola pendiente, cierre aun no confirmado y causa desconocida sin filtrar una ruta privada.
- :107, :114, :124 y :135: no mezclar peers, no heredar una muerte antigua con nuevo binding, eviction y rol offline.
- :176, :186, :193 y :198: 15/420 s, 10/10 s, atribucion global de frescura y preservacion de reintentos/abortos con razon futura.
- Las dos pruebas de control que pasan tambien contra .BEFORE son la que impide atribuir una muerte antigua a un binding nuevo y la de reintentos ya existentes. No se presentan como evidencia de cambio.

before_unittest.py carga exactamente los bytes .BEFORE bajo los nombres originales de modulo, imprime sus hashes y conserva __file__ para las rutas del paquete. No restaura ni altera el arbol compartido. red.log conserva incluso la primera corrida de montaje: tenia 5 errores del fixture por dos identificadores mal consultados (install_bound_peer es keyword-only, y la clave de snapshot es active). Se corrigieron en los tests; las dos corridas posteriores contra .BEFORE tienen solo fallos de comportamiento. La ultima da 13 fallos de 15 tests. green.log conserva todas las invocaciones, incluidas las repeticiones justificadas por el caso de replacement fallido.

Limitacion de atribucion: al rechazar sin target, el hint describe la ultima retirada conservada del peer, no certifica el run adoptado de un llamador concreto. Para un comando ya en cola, la causa corresponde a su instancia retirada. Los tombstones siguen siendo efimeros y acotados; tras eviction/reinicio o con causa no conocida se informa falta de causa.

No se inspecciono ni modifico daemon/juego/profiles de produccion, no se investigo quien termino procesos, no hubo lease real ni tools MCP. No se ejecuto suite completa. Las pruebas existentes que necesitan HTTP usan servidores de fixture en puertos efimeros, en serie.

Correccion sobre bundle: tools/build_native_launcher.py:53-73 no incluye ninguno de los tres .py modificados en PACKAGED_MODULES. No se necesita resellado por estos cambios de fuente; no se ejecuto build ni se altero un pin. La aplicacion a procesos de produccion queda para el receptor.

Sin git add, commit ni stash. El indice conserva el hash inicial; memoria global, HANDOFF y pipeline_feedback quedan FUERA DE MI ALCANCE por la lista exclusiva de escritura. Esta entrega es el handoff durable autorizado para Claude.
