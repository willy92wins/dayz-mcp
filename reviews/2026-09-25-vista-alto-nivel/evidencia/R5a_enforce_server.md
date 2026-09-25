<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == R5a_enforce_server.md findings: 12 {'EXACT': 12}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->



## Resumen
El bridge de servidor separa razonablemente transporte HTTP (poll/result callbacks, RestContext, key en query) de dominio (Dispatch y verbos), pero concentra todo en `MCPBridge.c`, 3.473 líneas, sin clase base común con el cliente. El flujo es poll→parse→dispatch→result, con trabajo diferido vía jobs para spawn/seat. La robustez tiene puntos buenos (backoff, cota de 32 pending, guardas de callback), pero deja riesgos claros: resultados POST fallidos se pierden, los spawn timeout pueden dejar objetos registrados, los jobs no tienen cota visible y un flag de deferencia tras `world_spawn` afecta al resto del lote. El tick 5 Hz por defecto es aceptable, pero readiness por spawn, reencoding de caps y `GetWorldSize()` repetido son candidatos a cachear o espaciar.

## A. Flujo

```text
HECHO:
MissionServer.c:19
OnUpdate(timeslice)
  -> MCPBridge.c:102
     OnTick(timeslice)
       -> MCPBridge.c:113
          ProcessJobs()
       -> MCPBridge.c:114
          DrainPending()
       -> MCPBridge.c:135
          StartPoll()

HECHO:
MCPBridge.c:228
StartPoll()
  -> MCPBridge.c:249
     request = "poll?key=" + m_Key
  -> MCPBridge.c:250
     + "&ver=" + GetPollVersion()
  -> MCPBridge.c:251
     + "&caps=" + EncodeQueryValue(SERVER_CAPABILITIES)
  -> MCPBridge.c:256
     m_Ctx.GET(cb, request)

HECHO:
MCPCallbacks.c:25
MCPPollCallback.OnSuccess(data, dataSize)
  -> MCPBridge.c:259
     OnPollSuccess(data, dataSize)
       -> MCPBridge.c:270
          deserialize MCPCommandBatch
       -> MCPBridge.c:297
          loop comandos
             -> MCPBridge.c:312
                Dispatch(command)
                // si no es diferido
             -> MCPBridge.c:308
                QueuePendingOrFail(command)
                // diferido

HECHO:
MCPBridge.c:451
Dispatch(command)
  -> HECHO:
     MCPBridge.c:465..559
     ramas por `command.cmd`
  -> HECHO:
     MCPBridge.c:568
     PostResult(result)
     // cuando el comando termina en el mismo tick

HECHO:
MCPBridge.c:603
m_Jobs.Insert(job.id, job);
MCPBridge.c:604
m_RuntimeObjects.Insert(job.id, spawned);
  -> INFERENCIA:
     el trabajo asíncrono ya no responde en este Dispatch

HECHO:
MCPBridge.c:2976
ProcessJobs()
  -> MCPBridge.c:2994
     IsJobReady(job)
        -> MCPBridge.c:2996
           PostJobSuccess(job)
  -> MCPBridge.c:3001
     else if (m_ElapsedS > job.deadline_s)
        -> MCPBridge.c:3003
           PostJobTimeout(job)

HECHO:
MCPBridge.c:3290
PostResult(result)
  -> MCPBridge.c:3302
     serializer.WriteToString(result, false, body)
  -> MCPBridge.c:3312
     m_CallbackRefs.Insert(cb)
  -> MCPBridge.c:3318
     m_Ctx.POST(cb, resultRequest, body)
```

Transporte común, visible en el material:
- `RestApi`, `RestContext`, key en query, callback GET/POST, backoff de poll, reutilización de callbacks, serialización de resultado, reencargo de key tras fallo persistente.
- HECHO: key va en query porque solo hay `SetHeader("application/json")` en `MCPBridge.c:210`.

Lógica de dominio:
- Validación de args, `GetObjectsAtPosition3D`, spawn, delete, teleport, inventario, tiempo/clima, raycast, telemetry, inspect, registry de objetos de runtime.
- HECHO: `SERVER_CAPABILITIES` en `MCPBridge.c:26` es lista informativa; no bloquea, pero se usa en poll.

## B. Extracción

[INFERENCIA]: la duplicación real del 23 % y las 30 funciones homónimas con `MCPClientBridge.c` sugieren una clase base de transporte, aunque no hay material del cliente para confirmar más allá de la lista dada por el receptor.

Base propuesta `[VERIFICAR-API]`: `MCPTransportBridge` o nombre equivalente.

Subir a base común:
- Inicialización HTTP: `TryInit` (`MCPBridge.c:138`) solo si la carga de config se mantiene común: `MCPConfig` en `MCPMessages.c:5`.
- Solicitud de poll y construcción de query: `StartPoll` (`MCPBridge.c:228`) con una subclase o método protegido que aporte caps/instance propios.
- Estados de poll: `OnPollSuccess` (`MCPBridge.c:259`), `OnPollError` (`MCPBridge.c:365`), `OnPollTimeout` (`MCPBridge.c:372`), `OnPollFail` (`MCPBridge.c:378`).
- Result POST común: `PostResult` (`MCPBridge.c:3290`) si ambos puentes publican `MCPResult`.
- Callbacks: `MCPPollCallback` y `MCPResultCallback` (`MCPCallbacks.c:1`, `MCPCallbacks.c:56`).
- Utilidades sin dominio: `EncodeQueryValue` (`MCPBridge.c:2227`), `IsFiniteFloat` (`MCPBridge.c:2645`), `StringHasPrefix` (`MCPBridge.c:2630`), gestión de refs/reciclado (`MCPBridge.c:3327`, `MCPBridge.c:3344`, `MCPBridge.c:3367`).
- Cierre: `Shutdown` (`MCPBridge.c:3381`) debe desconectar transporte, dejar el dominio a subclase.

Dejar en cada puente:
- Servidor: `Dispatch` (`MCPBridge.c:451`), verbos del mundo, validaciones de spawn/position/raycast, jobs de spawn/seat, registry de objetos de runtime, consultas de jugadores del servidor.
- Cliente: [INFERENCIA] hooks visibles en `MissionGameplay.c:16` (`ReleaseGameFocus`) y `MissionGameplay.c:38` (`OnMissionKeyPress`) indican lógica ligada a foco y teclado, pero no puedo extraer más sin el archivo cliente.

Qué puede diferir de verdad, visible en material:
- HECHO: `MissionServer.c:5` llama a `MCPBridge.ShutdownInstance()` y `MissionServer.c:26` a `MCPBridge.OnTick`.
- HECHO: `MissionGameplay.c:5` usa `MCPClientBridge.ShutdownInstance()` y `MissionGameplay.c:16` invoca métodos extra como `ReleaseGameFocus()`.
- [INFERENCIA]: si el cliente usa otro tipo de resultado, el transporte debe publicarse por tipo común o por `PostObject`, pero eso sería API nueva `[VERIFICAR-API]`.

## C. Robustez

1. Diferencia parcial de lotes tras `world_spawn`.
   - HECHO: `MCPBridge.c:295` crea `deferFromWorldSpawn`, `MCPBridge.c:302` lo pone a true, `MCPBridge.c:306` lo consulta para el resto del bucle.
   - Escenario: un lote devuelve `query_player_state`, `world_spawn`, `query_all_players`. Tras el spawn, los comandos posteriores entran en pending aunque no dependan del spawn, aumentando latencia y riesgo de `bridge_queue_full` si el lote es grande.

2. Cola de pending con cota, jobs sin cota visible.
   - HECHO: `MCPBridge.c:4` define `MAX_PENDING = 32`; `MCPBridge.c:351` rechaza si está llena con `bridge_queue_full`.
   - HECHO: `MCPBridge.c:232` bloquea nuevos polls usando también `m_Jobs.Count()`.
   - Escenario: si un consumidor emite muchos spawn/seat sin limpieza, `m_Jobs` puede crecer y bloquear poll; los comandos pendientes entonces llegan a 32 y fallan con `bridge_queue_full`, aunque el fallo de poll sea otro.

3. Timeout de spawn deja objeto registrado.
   - HECHO: `MCPBridge.c:603` crea job, `MCPBridge.c:604` registra objeto en runtime.
   - HECHO: `MCPBridge.c:3001` timeout llama a `PostJobTimeout`, que en `MCPBridge.c:3182` publica resultado, pero no elimina de `m_RuntimeObjects`.
   - Escenario: spawn repetido de un objeto que nunca aparece por flags/mundo; cada timeout añade entrada a registry. Solo `object_delete` (`MCPBridge.c:630`) limpia a mano.

4. Fallo POST de resultado se pierde.
   - HECHO: `MCPBridge.c:3357` loguea error de result, pero no reencola.
   - HECHO: `MCPCallbacks.c:87` llama a ese error y `MCPCallbacks.c:94` desconecta el callback para no reutilizarlo.
   - Escenario: daemon se cae justo tras un resultado local. El job/comando ya se limpió, el resultado se descarta; el agente queda sin respuesta.

5. Fallo de inicialización solo loggea la primera vez.
   - HECHO: `MCPBridge.c:219` con guard `m_InitFailureLogged`.
   - Escenario: falta RestApi, luego RestContext falla por otro motivo. El log inicial impide ver la segunda razón durante el intento fallido repetido.

6. Reencargo de key no adopta URL ni RestContext.
   - HECHO: comentario en `MCPBridge.c:419` indica que URL nueva no se adopta en el camino de fallo de poll.
   - HECHO: `ReloadKeyAfterFailure` (`MCPBridge.c:421`) solo cambia `m_Key` y resetea backoff.
   - Escenario: daemon cambia puerto y se relanza con nueva URL en config. El bridge puede continuar con contexto viejo hasta reinicio de misión.

7. Guardas correctas frente a callbacks huérfanos.
   - HECHO: `MCPCallbacks.c:21` descarta callbacks de poll inactivos.
   - HECHO: `MCPBridge.c:360` compara identidad `IsActivePollCallback`.
   - Esto reduce reentrada del poll tras timeout/error; no es un riesgo, pero es el mecanismo actual.

8. Recursos de callback acotados solo en admisión de poll.
   - HECHO: `MCPBridge.c:6` define `MAX_CALLBACK_REFS = 128`; `MCPBridge.c:232` reserva margen.
   - HECHO: `MCPBridge.c:3327` adquiere callbacks de resultado de forma libre.
   - Riesgo no observado: si la cantidad de trabajos asíncronos genera muchos results en ráfaga, la admisión evita más polls, pero no parece limitar directamente nuevos `PostResult` individuales.

## D. Tick

HECHO:
- Por cada `OnTick`, si no hay configuración, se intenta `TryInit` (`MCPBridge.c:107`, `MCPBridge.c:138`).
- Una vez configurado, siempre se ejecutan `ProcessJobs` (`MCPBridge.c:113`) y `DrainPending` (`MCPBridge.c:114`).
- El poll no es por tick: se acumula `timeslice` y se compara con `interval + backoff` (`MCPBridge.c:126-130`).
- HECHO: `m_PollHz = 5.0` por defecto (`MCPBridge.c:63`) y se puede ajustar con config hasta 60 (`MCPBridge.c:197`).
- HECHO: máximo 4 dispatch por tick en pending (`MCPBridge.c:3`) y 4 comandos sincronizados directos por poll (`MCPBridge.c:309`).

Costes candidatos a reducir:
1. Readiness de spawn por tick por job.
   - HECHO: `IsSpawnReady` (`MCPBridge.c:3028`) hace `GetObjectsAtPosition3D` con radio 8 (`MCPBridge.c:12`) y recorre resultados (`MCPBridge.c:3040`).
   - Si hay varios spawn jobs sin terminar, ese coste se repite cada tick.

2. Reencoding de caps en cada poll.
   - HECHO: `MCPBridge.c:251` llama a `EncodeQueryValue(SERVER_CAPABILITIES)` en cada poll.
   - Las caps son constantes, pero la función recorre caracteres uno a uno (`MCPBridge.c:2237`).

3. `GetWorldSize()` repetido en validaciones de posición.
   - HECHO: `ValidateSpawnArgs` usa `GetGame().GetWorld().GetWorldSize()` (`MCPBridge.c:2695`).
   - HECHO: `ValidatePositionArgs` usa el mismo patrón (`MCPBridge.c:2751`).
   - El tamaño de mundo parece estático durante la sesión; no se ve necesidad de reobtenerlo por comando.

4. Búsqueda de jugadores por lista global cuando se usa fallback.
   - HECHO: `GetFirstHuman` (`MCPBridge.c:2806`), `FindHumanByUid` (`MCPBridge.c:2825`) y `ResolvePlayer` (`MCPBridge.c:2854`) limpian y rellenan `m_Players` con `GetGame().GetPlayers`.
   - Esto solo corre en comandos, pero puede repetirse si el agente hace muchas consultas seguidas.

Qué cachear, espaciar o condicionar:
- Cachear a primera inicialización:
  - caps ya codificadas.
  - versión de poll si no es desconocida; ya existe `m_PollVersion` (`MCPBridge.c:34`, `MCPBridge.c:2205`).
  - tamaño de mundo si se dispone de él tras init.
- Espaciar:
  - readiness de spawn con un cooldown corto por job, p. ej. revisar solo si pasan N veceslices o si la posición ha cambiado lo suficiente.
  - no intentar 4 dispatch de pending si el último dispatch fue caro y no hay backlog crítico; pero eso requeriría un medidor de gasto no visible.
- Condicionar:
  - no reencargar `worldSize` si ya se cacheó.
  - no reencoding de caps si `SERVER_CAPABILITIES` no cambia nunca.

## E. Versionado

HECHO del material:
- `MCP_BRIDGE_VERSION = "10"` está en `MCPMessages.c:1`.
- El poll manda esa versión con juego en `MCPBridge.c:2216-2219`.
- `SERVER_CAPABILITIES` es censo informativo (`MCPBridge.c:20-26`).

Problema:
- Un PBO desplegado puede tener 20 cambios de verbos sobre la misma versión literal y el daemon solo compara `"10"`; por HECHO del receptor, ese es el único comparativo actual.
- El bridge no tiene código visible para comparar su versión contra una esperada, salvo si se añade a config/daemon.

Plan de detección fiable para PBO desfasado:
1. Definir en el daemon una lista de versiones de bridge compatibles por entorno:
   - `supported_bridge_versions = ["11"]`
   - `legacy_bridge_versions = ["10"]`
   - opcional `pbo_required_after = fecha`.
2. El daemon ya recibe `ver=` en el poll (`MCPBridge.c:250`).
   - HECHO: el formato enviado es `MCP_BRIDGE_VERSION~gameVersion` codificado (`MCPBridge.c:2216-2219`).
   - El daemon debe parsear la parte anterior a `~` y registrarla por sesión/instancia.
3. Para PBO nuevos, exigir `MCP_BRIDGE_VERSION` monótona en el daemon.
   - HECHO: el literal es único en `MCPMessages.c:1`; subirlo a `"11"` tras un cambio incompatible es suficiente como marca.
4. Para PBO ya desplegados con `"10"`:
   - No esperar a que el PBO viejo muestre el error.
   - Que el proxy/cliente o el daemon responda a los tools del agente antes de encolar un comando al juego:
     - error estructurado tipo:
       `bridge_outdated`,
       `actual=10`,
       `esperada=11`,
       `accion=Reempaqueta el addon DayZ_MCP`.
   - Esto no rompe despliegues viejos porque se aplica en el daemon/agente, no en el PBO viejo.
5. Si se quiere forzar que un PBO viejo falle visiblemente:
   - El daemon responde un comando desconocido, p. ej. `version_required`.
   - HECHO: un comando no reconocido termina en `unknown_command` (`MCPBridge.c:560-563`) y se publica como error (`MCPBridge.c:566-568`).
   - El agente puede interpretar ese fallo si sabe que la sesión tiene bridge viejo, pero es más fiable el bloqueo directo del daemon.
6. Compatibilidad progresiva:
   - Permitir `"10"` en modo degradado o con margen de fecha.
   - Después, marcarlo incompatible sin tocar el código del PBO viejo.

Necesario para PBO futuros:
- Añadir a config un campo opcional como `expectedBridgeVersion` en `MCPConfig` (`MCPMessages.c:5`) `[VERIFICAR-API]` si se usa nueva API de serialización/campos nuevos.
- En init, si la config exige versión concreta y `MCP_BRIDGE_VERSION` no coincide, loggear y/o bloquear comandos, pero no bloquear la conexión completa para permitir diagnósticos.

## F. Dispatch

HECHO actual:
- `Dispatch` es una cadena de comparaciones por string en `MCPBridge.c:451-564`.
- El final de la cadena responde `unknown_command` (`MCPBridge.c:560-563`).
- `SERVER_CAPABILITIES` es una lista separada (`MCPBridge.c:26`) que, por su comentario, debe mantenerse sincronizada con el dispatcher a mano.

Qué pasa al añadir 30 a 60 verbos:
- Cada verbo añade más ramas de string en un método ya enorme.
- La lista de caps se vuelve más fácil de desincronizar.
- Validaciones y resultados se dispersan en más métodos.
- `MCPResult` ya es una megaclase con muchos campos opcionales (`MCPMessages.c:433-503`); más verbos aumentan campos o reutilización confusa de campos.
- [INFERENCIA]: el coste principal será mantenibilidad y riesgo de errores de entrada, no tanto CPU del dispatch, porque las comparaciones son baratas frente a `GetObjectsAtPosition3D` u otros costes de mundo.

Patrón Enforce válido propuesto:
1. Mantener el objeto de comandos y resultados, pero añadir un registro de handlers con clases concretas.
2. Interfaz base `[VERIFICAR-API]`:
   - `bool Handles(string cmd)`
   - `bool Execute(MCPCommand command, MCPResult result, MCPBridge bridge)`
3. Registro explícito con objetos concretos, sin reflexión:
   - una lista/array de handlers,
   - un método inicial que los crea uno a uno.
4. `Dispatch` común:
   - validar comando vacío,
   - recorrer registro hasta encontrar handler,
   - si ninguno, `unknown_command`.
5. Cada handler contiene:
   - validación de args,
   - lógica de dominio,
   - flags de async si procede.

Riesgo del patrón:
- Los handlers necesitarían acceso a utilidades de `MCPBridge`.
- HECHO: muchos helpers actuales son `protected` en `MCPBridge` (p. ej. `FindUniqueObjectNearType` en `MCPBridge.c:1720`).
- Enforce no permite aquí `friend`; necesitarías exponer una fachada estrecha `[VERIFICAR-API]` o mantener handlers como métodos del bridge.

Alternativa menos invasiva:
- Generar o mantener el if/else, pero centralizar:
  - un registro de strings de comando,
  - validación común de args,
  - una tabla de versiones/capacidades.
- Menos bonito, pero reduce riesgo en 60 verbos sin cambiar el modelo de acceso a estado del bridge.

## G. Propuestas priorizadas

1. **Cachear partes estáticas de la query de poll**
   - Coste: S
   - Riesgo: bajo
   - Cambio:
     - calcular `EncodeQueryValue(SERVER_CAPABILITIES)` una vez,
     - cachejar también `GetPollVersion()` cuando no sea desconocida.
   - Verificación:
     - en juego: comparar `Log("config loaded ...")` y polls siguientes; verificar que la query no cambia salvo por key/inst;
     - offline: si hay test de request, comparar strings antes/después.

2. **Espaciar el readiness de spawn**
   - Coste: M
   - Riesgo: medio
   - Cambio:
     - no revisar cada tick si no pasa un cooldown corto;
     - si el objeto se mueve, reintentar con menor frecuencia o tras cambio de posición.
   - Verificación:
     - en juego: encolar 10-20 spawns y medir latencia de respuesta y FPS/servidor;
     - offline: si hay harness, verificar que readiness se dispara menos veces sin perder éxito.

3. **Cotar jobs por tipo y limpiar registry en timeout de spawn**
   - Coste: M
   - Riesgo: medio
   - Cambio:
     - límite de jobs de spawn/seat por clase;
     - en `PostJobTimeout` de tipo spawn, decidir si se elimina de `m_RuntimeObjects` o se marca como muerto;
     - si se elimina, devolver error claro de limpieza.
   - Verificación:
     - en juego: forzar timeouts de spawn y comprobar que el puente sigue aceptando nuevos comandos tras varios fallos;
     - offline: si hay test, verificar que el map no crece indefinidamente.

4. **Reintentar resultados POST fallidos con cota**
   - Coste: M
   - Riesgo: medio
   - Cambio:
     - encolarse resultados fallidos con backoff;
     - límite de reintentos por id para no duplicar en caso de timeout ambiguo.
   - Verificación:
     - en juego: parar el daemon un instante durante un resultado y comprobar que finalmente llega una única respuesta;
     - offline: mock de REST para 401/500/timeout y verificar reentrega.

5. **Compuerta de versión de PBO en el daemon, con error accionable para el agente**
   - Coste: L
   - Riesgo: medio-alto
   - Cambio:
     - daemon acepta/rechaza por versión de bridge observada en poll;
     - para sesiones incompatibles, tools del agente fallan antes de encolar al juego, con `actual`, `esperada` y acción recomendada.
   - Verificación:
     - offline: lanzar daemon con versión requerida falsa y simular bridge viejo;
     - en juego: desplegar PBO 10 contra daemon que exige 11 y confirmar que el agente recibe “reempaqueta” sin ejecutar comandos de mundo.

6. **Registro de handlers de comandos, con acceso controlado al puente**
   - Coste: L
   - Riesgo: medio
   - Cambio:
     - sustituir el if/else gigante por un registro de handlers concretos;
     - mantener una capa de comandos pequeños y un contexto público mínimo `[VERIFICAR-API]`.
   - Verificación:
     - migrar 2-3 comandos sencillos;
     - en juego, comparar comportamiento de 5 verbos actuales contra su versión en handler;
     - si hay tests, correr suite de regresión de verbos antes y después.

## H. Valoración

Arquitectura: **6/10**
- HECHO: hay una separación conceptual clara entre transporte, callbacks, mensajes y dominio (`MCPCallbacks.c`, `MCPMessages.c`, `MCPBridge.c`).
- HECHO: el puente concentra demasiadas responsabilidades en una sola clase de 3473 líneas, según HECHO VERIFICADO.
- [INFERENCIA]: la falta de base de transporte con el cliente, confirmada por el 23 % compartido y 30 funciones homónimas, es la mayor deuda estructural.

Robustez: **7/10**
- HECHO: hay backoff con tope, cota de pending, guardas de callbacks, timeouts de jobs y manejo de parseo de poll.
- HECHO: los fallos de POST de resultado se pierden (`MCPBridge.c:3357`), y los timeouts de spawn pueden dejar registry de objetos.
- HECHO: el guard de poll activo en `MCPCallbacks.c:21` reduce reentrada, pero hay más riesgo en resultados y limpieza de recursos.

Rendimiento: **7/10**
- HECHO: el throttle de poll, `MAX_DISPATCH_PER_TICK = 4` y cota de pending frenan picos simples (`MCPBridge.c:3-5`).
- HECHO: el default de 5 Hz (`MCPBridge.c:63`) parece razonable para un puente de agente, pero no veo métricas de tiempo real.
- HECHO: readiness por tick, reencoding de caps y `GetWorldSize()` por validación son costes visibles y reducibles.

Mantenibilidad: **6/10**
- HECHO: 100 funciones >80 líneas y 17 >200 líneas, según HECHO VERIFICADO.
- HECHO: 20+ verbos están en una cadena de string-comparison en un archivo que ya asume config, HTTP, jobs, mundo y serialización.
- HECHO: las caps se mantienen a mano (`MCPBridge.c:22-26`), con riesgo creciente al 30-60 verbos.

## Hallazgos
F01 | P1 | addon/scripts/5_Mission/MCPBridge.c:3038 | rendimiento | `IsSpawnReady` ejecuta una búsqueda espacial completa por job pendiente y por tick, escalando con el número de spawn jobs. | EVID: GetGame().GetObjectsAtPosition3D(pos, SPAWN_READY_RADIUS, m_ReadyObjects, m_ReadyProxyCargos); | FIX: espaciar readiness por cooldown corto y reintentar tras cambio de posición
F02 | P2 | addon/scripts/5_Mission/MCPBridge.c:251 | rendimiento | El request de poll re-encodea cada vez las capabilities, aunque sean constantes de clase. | EVID: request = request + "&caps=" + EncodeQueryValue(SERVER_CAPABILITIES); | FIX: cachear las caps codificadas tras init
F03 | P2 | addon/scripts/5_Mission/MCPBridge.c:2695 | rendimiento | La validación de spawn obtiene `GetWorldSize()` por comando, aunque el mundo no cambia en la sesión. | EVID: float worldSize = GetGame().GetWorld().GetWorldSize(); | FIX: cachear worldSize tras init o tras primer éxito
F04 | P1 | addon/scripts/5_Mission/MCPBridge.c:306 | robustez | Un solo `world_spawn` en un lote fuerza a diferir los comandos siguientes, afectando a órdenes independientes. | EVID: if (deferFromWorldSpawn) | FIX: diferir solo el spawn o aislar el flag por comando
F05 | P1 | addon/scripts/5_Mission/MCPBridge.c:232 | robustez | La admisión de poll se bloquea también por `m_Jobs`, pero no se ve una cota de jobs de mundo. | EVID: if (m_CallbackRefs.Count() + m_Pending.Count() + m_Jobs.Count() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS) | FIX: añadir cotas por tipo de job y backpressure explícito
F06 | P2 | addon/scripts/5_Mission/MCPBridge.c:604 | robustez | El registro de objetos de runtime se inserta al encolar spawn, pero el timeout no elimina esa entrada visible en `PostJobTimeout`. | EVID: m_RuntimeObjects.Insert(job.id, spawned); | FIX: retirar o marcar la entrada al expirar el job de spawn
F07 | P1 | addon/scripts/5_Mission/MCPBridge.c:3359 | robustez | Un fallo POST de resultado solo loguea error y el callback se desconecta, sin reintento. | EVID: Log("result post error=" + errorCode); | FIX: encolar reintentos con cota y backoff
F08 | P1 | addon/scripts/5_Mission/MCPMessages.c:1 | versionado | La marca de compatibilidad es un literal único y el bridge no tiene lógica visible para exigir versión esperada. | EVID: const string MCP_BRIDGE_VERSION = "10"; | FIX: añadir compatibilidad en daemon y opcional versión esperada en config
F09 | P2 | addon/scripts/5_Mission/MCPBridge.c:556 | mantenibilidad | El dispatch principal sigue siendo una cadena de if por nombre de comando, sin metadatos centralizados por verbo. | EVID: else if (command.cmd == "entities_query") | FIX: migrar a registro de handlers o a tabla de comandos
F10 | P2 | addon/scripts/5_Mission/MCPBridge.c:362 | robustez | El puente filtra callbacks de poll inactivos comparando identidad, lo que evita que un callback viejo procese el batch actual. | EVID: return cb == m_PollCallback; | FIX: mantener la identidad y añadir log cuando un callback sea descartado
F11 | P2 | addon/scripts/5_Mission/MCPCallbacks.c:82 | transporte | Los callbacks de éxito de resultado se reciclan, pero los de error no vuelven a usarse a propósito para evitar reutilizar identidades fallidas. | EVID: m_Bridge.RecycleResultCallback(this); | FIX: documentar que el reciclado solo cubre éxito y evaluar reintentos con nuevo identity
F12 | P2 | addon/scripts/5_Mission/MCPBridge.c:1 | arquitectura | La clase del puente del servidor concentra a la vez transporte, cola, jobs, validación y semántica de mundo en una sola jerarquía de 3473 líneas. | EVID: class MCPBridge : Managed | FIX: extraer base de transporte común a ambos puentes
## LO QUE NO PUDE VERIFICAR
- El contenido real de `MCPClientBridge.c`; solo puedo inferir la base común por los hechos verificados por el receptor y por los hooks visibles de cliente en `MissionGameplay.c`.
- Si las 30 funciones homónimas tienen semántica idéntica o solo nombres compartidos; el material no incluye el cliente.
- La intención original del bloqueo de comandos posteriores a `world_spawn`: es un bug, una restricción intencional o un efecto colateral aceptado.
- Coste temporal real de `GetObjectsAtPosition3D`, `GetPlayers`, `GetWorldSize` y `EncodeQueryValue`; no hay profiler ni métricas en el material.
- Si `SetAnimationPhaseNow` pertenece a la familia `*_now` prohibida por el contexto de ingeniería o es una excepción segura de Enforce.
- Uso efectivo de `MCPJobRunner.c` en el puente de servidor; veo la clase, pero no su cableado en `MCPBridge.c`.
- Implementación del daemon Python, salvo los hechos verificados por el receptor sobre comparación literal de versión y no bloqueo por caps.
- Existencia de APIs concretas para la base de transporte y los handlers de comando sin escribir código nuevo de Enforce.
## ¿Qué puede estar mal en la premisa de este encargo?
- La duplicación 23 % con el cliente puede medir líneas literales compartidas, no necesariamente la cantidad real de lógica que conviene extraer a una base común.
- El 5 Hz por defecto puede ser suficiente para el uso de agente, y el impacto de readiness/reencoding puede quedar por debajo de otros costes si no se mide con profiler.
- Mantener 20+ verbos en un solo puente puede ser deliberado si los verbos comparten heavily estado de mundo, jobs y registry; extraer demasiado podría crear acoplamiento disfrazado.
- La versión única `"10"` quizá se haya conservado a propósito para no romper despliegues viejos, aunque eso convierta a la versión en un contrato débil.
- Un registro de handlers puede empeorar la legibilidad en Enforce si obliga a exponer más API interna del puente o a crear muchas clases pequeñas con estado compartido difícil de auditar.
GATE NO CORRIDO: revisión por API sin herramientas