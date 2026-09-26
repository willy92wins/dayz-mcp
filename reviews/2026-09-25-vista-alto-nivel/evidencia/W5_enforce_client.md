<!-- Salida CRUDA de una lane Qwen3.8-Flash-Next (GX10, ronda 4, contexto completo), sin editar. NO es el informe: puede contener errores.
     Verificación mecánica del receptor (EVID literal a ±10 líneas en HEAD 57269d2): == W5_enforce_client.md findings: 15 {'EXACT': 14, 'FAR': 1}
     Veredicto del receptor: ver ../INFORME.md §4 y §8. -->



## Resumen (máximo 8 líneas)
El cliente (`MCPClientBridge.c`, 4529 l.) es un solo clon de la maquinaria de transporte del servidor (`MCPBridge.c`, 3473 l.): 30 nombres homónimos, 23 % de líneas idénticas. La extracción es factible pero parcial: lo verbatim sube a base; el grueso diverge en literals por lado o en tipos concretos.
La robustez del transporte es sólida (backoff con recarga de key, watchdog de poll, guardia fail‑closed de entrada, colas con cota, autolimpieza del control por deadline).
Riesgos reales: resultado perdido si el POST falla y caída silenciosa si `m_Ctx` es nulo (sin log, a diferencia del servidor); un `camera_set` fallido deja el input bloqueado porque suprime el control antes de poder fallar; `IsFiniteFloat` del cliente es más laxo que el del servidor; `CountUiWidgetsNamed` recorre el árbol entero si el nombre no existe.
`MCP_CarScript` está bien acotado (deadline, reloj monótono, overflow) pero preasigna hasta 8192 muestras por traza y añade trabajo a todo `OnInput`.
La premisa "extraer base para quitar el 23 %" sobrepromete: una parte de ese 23 % son literales por lado (tag de log, token de cola, tipo de callback) que una base no deduplica sin perder identidad por lado.

## A. Flujo del cliente
Diagrama (líneas del material). `MCPClientBridge` es un `MCPJobRunnerOwner`; el motor llama a su `OnTick` desde `MissionGameplay.c`.

Cada frame (`MissionGameplay.c:20 OnUpdate` → `OnTick:295`):
1. `m_Tick++` (`:297`).
2. `m_JobRunner.Tick(timeslice, this)` (`:299`–`:302`) → `MCPJobRunner.c:90 Tick` → `:104 ProcessJobs` recorre el mapa de jobs CADA tick; un job terminado dispara `MCP_PostJob*` y por tanto un `PostResult`.
3. Si `!m_Configured` → `TryInit()` y `return` (`:304`–`:308`).
4. Primera vez en juego → `EnsureDialogHost()` (una sola vez, `:310`–`:314`).
5. `DrainPending()` despacha hasta `MAX_DISPATCH_PER_TICK=4` de `m_Pending` (`:316`, bucle `:703`–`:709`).
6. Si `m_PollInFlight`: acumula `m_PollInFlightS` y, a los 30 s, `AbandonInFlightPoll()` + `OnPollFail("watchdog")` y `return` (`:318`–`:333`).
7. Si `m_Pending > PENDING_POLL_THRESHOLD=8` → `return` (pausa admisión, `:335`–`:338`).
8. `m_Accum` vs `1/m_PollHz + m_Backoff`; al superar, `StartPoll()` (`:340`–`:348`).

Poll → dispatch → jobs → post:
- `StartPoll:464`: guardia de admisión por `OutstandingWork()` (`:473`); marca in‑flight; `EnsurePollCallback`+`HoldPollCallback` (`:483`–`:484`); petición a `m_PollCtx` con `peer=client` y `caps=CLIENT_POLL_CAPS` (`:485`–`:492`).
- `OnPollSuccess:524`: parse + `m_PollInFlight=false` + reset de backoff (`:532`,`:552`); primer `MAX_DISPATCH_PER_TICK` vía `Dispatch` en línea, el resto a cola (`:563`–`:570`).
- `Dispatch:743`: puerta fail‑closed `IsClientInGame()` primero (`:761`); if‑chain por comando. `postNow=true` responde ya (`:853`–`:856`).
- Commands asíncronos → job (`postNow=false`): cámara (`DispatchCameraSet:931`), entrar coche (`:977`), diálogo (`:1777`).
- `MCP_ProcessJob:2695`: cámara = apply→settle→report; coche = prep→report; diálogo = `m_Dialog.Tick` y listo cuando `dialog.state!=""` (`MCP_IsJobReady:2723`).
- Reporte: `MCP_PostJobSuccess:3412` construye el `MCPResult` y `PostResult:4281` (serializa, callback, POST a `result?key=…`).

Trabajo del coche fuera de los jobs: `CarScript.OnInput` (`MCP_CarScript.c:724`) cada tick del vehículo conduci‑do: autolimpieza por deadline (`:732`), gestión de motor/engranes, 5 setters (`:787`–`:791`), y siempre `MCPVehicleTrace.Capture(this, dt)` (`:796`), con autolimpieza por owner/overflow/reloj (`:239`,`:531`,`:52`).

## B. Robustez
Estados colgados y escapes, con escenario.

1) Resultado perdido / caída silenciosa (F01/F02). Escenario: el daemon reinicia o la conexión se corta a mitad de comando. Si `m_Ctx` ya es nulo (teardown), `PostResult` sale sin log alguno (`:4283`–`:4286`): el resultado terminal desaparece sin rastro. Si la POST responde error, `OnResultError` solo loggea (`:4320`) y el resultado se pierde igual. El servidor al menos loggea la caída una vez (`MCPBridge.c:3294`–`:3297`).

2) Input bloqueado por cámara fallida (F03). `ApplyCameraSet` suprime el control (`SuppressGameplay:3315`) y luego puede fallar (`camera_create_failed:3329`). `MCP_ProcessJob` termina el job y reporta el error, pero NINGÚN camino de fallo hace `RestoreGameplay()`. El jugador queda con `INPUT_EXCLUDE_ALL` y HUD oculto hasta un `restore_gameplay` o un siguiente job que lo haga. `ReleaseGameplay` sí restaura simulación/controles/HUD (`:4190`–`:4210`), pero el camino de error no lo invoca.

3) Validación laxa vs estricta (F04). El cliente rechaza solo NaN (`IsFiniteFloat:4023`–`:4031`); el servidor además rechaza `±float.MAX` (`MCPBridge.c:2651`). `MCP_ARG_FLOAT_UNSET == float.MAX` (`MCPMessages.c:2`); hoy ningún gate del cliente usa ese centinela (los defaults 0.0), pero un `radius/ttl/fov` desbordado pasaría como válido en el cliente y no en el servidor [INFERENCIA de uso futuro].

4) Recorrido sin cota si el nombre no existe (F07). `ResolveUiRoot`→`ResolveUniqueUiWidget` cuenta coincidencias y, si `name` no aparece, `count` nunca llega a 2 (`:2307`) y `CountUiWidgetsNamed` desciende TODOS los hijos recursivamente (`:2328`). Una ruta inexistente sobre un árbol profundo → DFS completo; `CollectUiNodes` sí se corta por número de nodos (`:2617`), pero eso no acota la profundidad.

5) Cola de resultados que bloquea polls (F05/F06). Cada `PostResult` crea un callback NUEVO y lo inserta (`:4297`–`:4298`). `OutstandingWork` cuenta `m_CallbackRefs` (`:446`) y `StartPoll` bloquea admisión al superar `MAX_CALLBACK_REFS-MAX_POLL_RESULTS` (`:473`). Escenario: 16 resultados colgados (sin completar) → el cliente deja de aceptar comandos nuevos.

6) Watchdog de poll solo en cliente (F09). El guardia de 30 s y `AbandonInFlightPoll` existen solo aquí (`:318`–`:333`). El servidor mantiene `m_PollInFlight` sin watchdog (`MCPBridge.c:116`–`:119`): si un callback de poll se pierde, el servidor no re‑polvea nunca más [INFERENCIA servidor].

7) `m_PollCtx` es un alias de `m_Ctx` (F10). `PollContextUrl` devuelve la url tal cual (`:588`), así que la "isolación" de polls viejas es solo descarte por identidad (comentario `:584`–`:585`); no hay separación real de contexto, y el reset se omite deliberadamente para no cancelar POSTs (`:598`–`:607`).

Mitigaciones que SÍ funcionan (mención): autolimpieza de control por deadline (`MCP_CarScript.c:732`), reloj monótono y overflow que paran la traza (`:252`,`:531`), cola con cota `MAX_PENDING=16` que responde `client_bridge_queue_full` (`:719`–`:721`), guardia anti‑reentrada de `Shutdown` (`:4370`), y `RestoreGameplay` idempotente con log latcheado (`:4179`–`:4210`).

## C. MCPBridgeBase
Las 30 funciones homónimas. `IGUAL` = cuerpo idéntico (sube tal cual). `DIFIERE` = mismo rol, contenido distinto. `SOLO‑NOMBRE` = mismo nombre, cosas ajenas al puente.

| Función | Cliente | Servidor | Veredicto |
|---|---|---|---|
| EncodeQueryValue | :4096 | :2227 | IGUAL (literal). Nota: ambos con bug de byte alto sign‑extend (F12) |
| GetPollVersion | :4068 | :2199 | IGUAL (usa `MCP_BRIDGE_VERSION` compartido) |
| VectorToArray | :3983 | :2622 | IGUAL |
| StringHasPrefix | :4053 | :2630 | IGUAL |
| DrainPending | :695 | :322 | IGUAL |
| PostCommandError | :4269 | :3214 | IGUAL |
| OnPollError | :609 | :365 | IGUAL (idéntico, mismo comentario) |
| OnPollTimeout | :616 | :372 | IGUAL |
| IsFiniteFloat | :4023 | :2645 | DIFIERE (servidor añade `±float.MAX`) — subir la versión estricta |
| Log | :4524 | :3468 | DIFIERE solo literal `"[MCP-CLIENT] "` vs `"[DayZ-MCP] "` |
| LogInitFailure | :430 | :217 | DIFIERE solo el `"client "` en el mensaje |
| ReloadKeyAfterFailure | :665 | :421 | DIFIERE solo prefijo `"client "` + guardia URL‑no‑adoptada |
| OnResultSuccess/Error/Timeout | :4313/:4318/:4323 | :3352/:3357/:3362 | DIFIERE solo prefijo `"client "`; ambos solo log |
| QueuePendingOrFail | :712 | :339 | DIFIERE solo token `"client_bridge_queue_full"` vs `"bridge_queue_full"` |
| IsActivePollCallback | :575 | :360 | DIFIERE solo tipo del parámetro (clases de callback distintas) |
| Get | :260 | :92 | DIFIERE (singleton estático de tipo concreto) |
| ShutdownInstance | :270 | :3459 | DIFIERE (estático + `new` concretos) |
| TryInit | :351 | :138 | DIFIERE: cliente añade 2º contexto poll + log `"client config loaded"` |
| StartPoll | :464 | :228 | DIFIERE: 2º contexto + `HoldPollCallback` + `peer=client` + `caps` distintas |
| OnPollSuccess | :524 | :259 | DIFIERE: servidor difiere todo desde `world_spawn` (`:295`–`:309`) |
| OnPollFail | :622 | :378 | DIFIERE: cliente limpia `m_PollInFlightS` + log `"client poll"`; backoff idéntico |
| PostResult | :4281 | :3290 | DIFIERE: cliente callback nuevo sin pooling y sin log de caída |
| ReleaseCallback | :4328 | :3367 | DIFIERE: cliente 2 arrays con while‑manual; servidor 1 con `.Find()` |
| Dispatch | :743 | :451 | DIFIERE (vocabulario de comandos y puerta fail‑closed distintos) |
| OnTick | :295 | :102 | DIFIERE (JobRunner + watchdog + diálogo vs `m_ElapsedS`+`ProcessJobs`) |
| Shutdown | :4364 | :3381 | DIFIERE (guardia reentrada + diálogo + cámara + coche) |
| GetGame | (uso) | (uso) | SOLO‑NOMBRE: global del motor, no método del puente |
| Print | (uso) | (uso) | SOLO‑NOMBRE: global del motor |

Diseño propuesto de `MCPBridgeBase : MCPJobRunnerOwner`:

Miembros a base (estado de transporte puro, sin juego/UI/cámara): `m_Ctx`, `m_PollCtx`, `m_Url`, `m_Key`, `m_PeerInstance`, `m_PollVersion`, `m_PollHz`, `m_Accum`, `m_Backoff`, `m_Tick`, `m_TickPollSent`, `m_TickPollCallback`, `m_PollInFlight`, `m_PollInFlightS`, `m_PollInFlightS`‑watchdog, `m_Configured`, `m_InitFailureLogged`, `m_Shutdown`, `m_ShutdownReentryLogged`, `m_ResultDropLogged` (adoptar del servidor), `m_CallbackRefs`, `m_PollCallbackRefs`, `m_PollCallback` (como referencia a una base `MCPPollCallbackBase` si se introduce; ver riesgo), y las constantes `KEY_RELOAD_BACKOFF_S`, `POLL_WATCHDOG_S`, `MAX_PENDING`, `MAX_DISPATCH_PER_TICK`, `PENDING_POLL_THRESHOLD`, `MAX_CALLBACK_REFS`, `MAX_POLL_RESULTS`.

Métodos que SUBEN a base:
- Verbatim: `DrainPending`, `OnPollError`, `OnPollTimeout`, `EncodeQueryValue` (tras fix F12), `GetPollVersion`, `VectorToArray`, `StringHasPrefix`, `PostCommandError`, e `IsFiniteFloat` en su versión STRICTA de servidor.
- Con hook `LogTag()` (devuelve `"[MCP-CLIENT] "` / `"[DayZ-MCP] "`): `Log`, `LogInitFailure`, `ReloadKeyAfterFailure`, `OnResultSuccess/Error/Timeout`. Cambia el texto de 6‑7 líneas del log → [VERIFICAR] grep del repo antes de fusionar.
- Con hook `QueueFullError()` (devuelve el token): `QueuePendingOrFail`.
- `TryInit`: a base la carga de config + validación loopback + clamp de `pollHz` + alta de contexto, con hook `OnContextsReady()` para que el cliente añada el 2º contexto (`m_PollCtx`).
- `StartPoll`: a base el gate (`OutstandingWork` virtual) + armado de petición, con hooks `Capabilities()`, `PollContext()` (cliente devuelve `m_PollCtx`, servidor `m_Ctx`), `PeerTag()` (`"&peer=client"` vs vacío).
- `OnPollSuccess`: a base el bucle parse/dispatch/queue; hook `DeferFromHere(command)` (servidor lo usa para `world_spawn`, cliente vacío).
- `PostResult`: a base serializar + construir petición + POST + log de caída (adoptar la del servidor); hook `AcquireResultCallback()` (cliente `new`, servidor pool).
- `OnTick`: a base el esqueleto tick/incrementos + poll; hooks `ProcessJobs()` (cliente delega a JobRunner; servidor a su modelo) y el watchdog a base con flag on/off (activable también para servidor → F09).
- `Dispatch`: a base solo el sobre (`BeginResult(id)` con los 3 stamps) y el `unknown_command`; cada puente conserva su if‑chain.
- `Shutdown`: a base la telemortaja de transporte (detach callbacks, reset de contextos por política, clear de colas, reset de flags, guardia de reentrada del cliente); hook `OnShutdownPre()` (cliente: diálogo terminal, unlink preview, `MCPCarDrive.Clear`, `MCPVehicleTrace.Abort`, `RestoreGameplay`, `ReleaseCamera`).
- `ReleaseCallback`: a base con `.Find()` sobre ambos arrays (el array de poll del servidor queda vacío).

Quedan en CLIENTE (`MCPClientBridge`): singleton concreto (`Get`, `m_Instance`, `ShutdownInstance`), puerta `IsClientInGame` (`:761`), todo el if‑chain cliente (`DispatchCamera*`, `Ui*`, `Vehicle*`, `ActionUse`, `KeyPress`, `Respawn`), host de diálogo, 3 overrides `MCP_PostJob*`/`MCP_ClearJobRefs`, `RestoreGameplay`/`SuppressGameplay`/`ReleaseCamera`/`DeleteOwnedCamera`/`ReleaseGameFocus`, builders de cámara, y las 61 funciones UI no‑homónimas (recursión, encode de ruta, snapshot).

Quedan en SERVIDOR: singleton propio (`MCPPool` de resultados `AcquireResultCallback`/`RecycleResultCallback`), su `ProcessJobs`, el modelo de jobs propio, `m_ElapsedS`/`m_Players`, y todos sus `Dispatch*` de mundo/objetos/inventario/clima/telemetría.

Nota de identidad: las 4 clases de callback (cliente 2, servidor 2) son tipos concretos usados para comparar identidad (`IsActivePollCallback`) y para `DetachBridge` en cascada. Compartir base de callback exige un tipo común y virtual; si no, quedan 4 clones que una base de bridge NO elimina.

## D. Coste por frame
- `CarScript.OnInput` (`MCP_CarScript.c:724`) se ejecuta CADA tick para cada vehículo con input local y, aunque no haya control ni traza, escribe dos estáticos (`s_TickEngineReady`/`s_TickThrottleSet`, `:728`–`:729`) y llama a `MCPVehicleTrace.Capture(this, dt)` (`:796`). `Capture` sale rápido si `!s_Active` (`:235`), pero el coste fijo por tick por vehículo está ahí.
- Con control activo, cada tick lee `EngineGetRPM` + `GetSpeedometerAbsolute` (`:740`–`:741`) y, si va alto de vueltas, `EngineGetRPMRedline` (`:767`); son 2‑3 getters de motor por tick del conductor.
- Con traza activa, `Capture` acumula `s_AccumS` y hace corta si no toca intervalo (`:245`–`:250`); el cuerpo pesado (`CaptureNow`, ~25 getters + 4 de ruedas, `:545`–`:678`) solo corre a 20–60 Hz por diseño.
- `OnContact` (`:799`) → `CaptureContact` hace 3 `IndexOf` por contacto si hay traza (`:280`–`:287`); se podría cachear la detección de zona de rueda.
- En el puente, `TryInit` queda barriado por `m_Configured` (`:304`), pero el `TryInit` de arranque relee fichero si falta (`:367`–`:372`); la recarga de fichero solo tras 4 s de backoff (`:648`–`:651`).
- Las cadenas `m_Pending`/callbacks se recorren con `while` manual en `ReleaseCallback` (`:4334`, `:4352`) — O(n) por callback completado.
- UI: un comando dispara DOS DFS (resolver + `BuildUiMatchedPath` que además recorre hermanos para el ordinal, `:2400`–`:2414`, O(ancho·profundidad)); no es por frame pero sí por comando.

## E. Propuestas (máx. 8)
1) Subir a base el núcleo verbatim + cluster "solo log‑tag" vía `LogTag()`. Coste M. Riesgo: 6‑7 líneas de log cambian de texto → [VERIFICAR] grep de `"[MCP-CLIENT]"` y frases `"client …"` antes de fusionar. Verificar offline: diff que confirme que los 8 cuerpos son idénticos a la copia más estricta; un poll de humo.
2) Adoptar `IsFiniteFloat` estricto como base. Coste S. Riesgo: cliente podría rechazar lo que antes aceptaba (hoy no hay centinela `float.MAX` en sus gates [INFERENCIA]). Verificar in‑game: `vehicle_control`/`action_use` con `radius/ttl=float.MAX` vs normal → esperable rechazar el centinela.
3) Añadir a base el log de caída de resultado (copiar `MCPBridge.c:3294`). Coste S. Riesgo: nulo. Verificar offline: forzar `PostResult` con `m_Ctx=null` y ver el log.
4) Pool de callbacks de resultado en cliente (espejar servidor, con "nunca reutilizar tras OnError"). Coste M. Riesgo: reutilizar identidad cuyo `OnError` puede repetirse. Verificar offline: 100 POST y comprobar que `m_CallbackRefs` no crece y que un POST completado se reutiliza.
5) Unificar `ReleaseCallback` a base con `.Find()` sobre ambos arrays. Coste M. Riesgo: comportamiento de `.Find()` con 2 arrays. Verificar offline: liberar un poll huérfano y un resultado.
6) Compartir watchdog de poll a base y ACTIVARLO en servidor (que hoy puede colgarse al perder un callback, F09). Coste M. Riesgo: cambia el servidor. Verificar offline/in‑game: simular pérdida de callback y ver que ambos se recuperan.
7) Cota de recorrido en `CountUiWidgetsNamed` (p. ej. máximo de nodos visitados) para el caso de nombre inexistente (F07). Coste M. Riesgo: un árbol legítimo muy grande podría cortar antes. Verificar offline: ruta inexistente sobre menú profundo → error sigue siendo `widget_not_found` y el tiempo queda acotado.
8) Fix del bug de codificación de bytes en `EncodeQueryValue` (compartido con `EncodeUiPathSegment`, que sí lo hace bien en `:2479`–`:2483`). Coste S. Riesgo: si el daemon compara la clave en claro, valores `>=0x80` pasarían a `?` tras el fix; la clave actual es ASCII (no la rompen hoy). Verificar offline: encodear a mano una cadena de bytes altos con el bucle corregido y contrastar con el literal `0..255`.

## F. Valoración
- **MCPClientBridge: 6/10.** Defensivo en transporte (watchdog `:321`, guardia reentrada `:4370`, puerta fail‑closed `:761`, backoff + recarga de clave `:648`–`:651`, cola con cota `:719`), y trata los traps de cámara (singleton `FreeDebugCamera` `:4230`, autolimpieza de ownership `:4223`). Le bajan: 4529 líneas y 60+ métodos en una clase mezclando transporte/UI/cámara/coche/diálogo/acción; la caída silenciosa de resultado sin log (`:4283`), el callback nuevo por POST (`:4297`), `IsFiniteFloat` laxo (`:4023`) y el DFS sin cota del resolver (`:2328`).
- **MCP_CarScript: 7/10.** Autolimpieza por deadline (`:732`), guardia de reloj monótono (`:252`) y overflow que corta a 8192 (`:531`) con autolimpieza por owner (`:239`). Le bajan: preasignación de 8192 objetos de muestra por arranque (`:199`), una estructura de 50+ campos (`:49`–`:109`), y `OnInput` modded con trabajo fijo por tick por vehículo incluso en reposo (`:728`, `:796`).
- **Duplicación con servidor: 4/10.** El 23 % no es clonado puro sino "copy‑fork" que YA divergió en semántica: `IsFiniteFloat` estricto vs laxo (`:2651` vs `:4023`), watchdog solo‑cliente (`:321`), log de caída solo‑servidor (`:3294`), y el bug de bytes presente en AMBOS (`MCPBridge.c:4109`). Un fix de un lado no llega al otro, y un merge "por nombre" elegiría una semántica u otra en silencio.

## Hallazgos
F01 | P1 | addon/scripts/5_Mission/MCPClientBridge.c:4283 | robustez | PostResult sale sin log si el contexto es nulo y el resultado terminal desaparece sin rastro | EVID: if (!m_Configured || !m_Ctx) | FIX: espejar la guardia log única de MCPBridge.c:3294
F02 | P1 | addon/scripts/5_Mission/MCPClientBridge.c:4320 | robustez | un POST de resultado fallido solo se loggea y el resultado se pierde igual que en servidor | EVID: Log("client result post error=" + errorCode); | FIX: reintentar terminal 1 vez y exponer un resultado degradado
F03 | P1 | addon/scripts/5_Mission/MCPClientBridge.c:3329 | robustez | ApplyCameraSet suprime el control antes de poder fallar y deja el input bloqueado al no restaurar en el camino de error | EVID: job.error = "camera_create_failed"; | FIX: RestoreGameplay() al devolver false en ApplyCameraSet
F04 | P2 | addon/scripts/5_Mission/MCPClientBridge.c:4023 | duplicación | IsFiniteFloat del cliente rechaza solo NaN y el servidor además rechaza float.MAX; mismos nombres, semántica distinta | EVID: protected bool IsFiniteFloat(float value) | FIX: subir la copia estricta a base
F05 | P1 | addon/scripts/5_Mission/MCPClientBridge.c:4297 | coste | cada PostResult crea un callback RestCallback nuevo sin pool, a diferencia del servidor | EVID: MCPClientResultCallback cb = new MCPClientResultCallback(this); | FIX: pool de resultados como el servidor
F06 | P2 | addon/scripts/5_Mission/MCPClientBridge.c:473 | robustez | OutstandingWork cuenta callbackRefs y bloquea StartPoll si 16 POST quedan colgadas, congelando comandos | EVID: OutstandingWork() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS | FIX: caducar callbacks colgados
F07 | P2 | addon/scripts/5_Mission/MCPClientBridge.c:2328 | coste | CountUiWidgetsNamed recorre TODOS los hijos recursivamente si el nombre no existe, sin cota de nodos ni profundidad | EVID: CountUiWidgetsNamed(child, name, match); | FIX: añadir máximo de nodos visitados
F08 | P2 | addon/scripts/4_World/MCP_CarScript.c:199 | coste | Start de traza preasigna hasta 8192 objetos MCPVehicleTraceSample por arranque aunque el trazo sea corto | EVID: s_Samples.Set(index, new MCPVehicleTraceSample()); | FIX: crecimiento bajo demanda o aplanado
F09 | P2 | addon/scripts/5_Mission/MCPClientBridge.c:321 | robustez | el watchdog de poll solo existe en cliente; el servidor mantiene m_PollInFlight sin él y se colgaría al perder un callback [INFERENCIA] | EVID: if (m_PollInFlightS >= POLL_WATCHDOG_S) | FIX: compartir watchdog a base y activar en servidor
F10 | P2 | addon/scripts/5_Mission/MCPClientBridge.c:588 | transporte | el 2º contexto de poll es no‑op porque PollContextUrl devuelve la url igual y m_PollCtx queda igual a m_Ctx | EVID: return url; | FIX: base o alias explícito con reset por contexto
F11 | P2 | addon/scripts/5_Mission/MCPClientBridge.c:665 | robustez | ReloadKeyAfterFailure recarga la clave pero ignora a propósito un cambio de url, que exigiría re‑init | EVID: protected void ReloadKeyAfterFailure() | FIX: reintento de re‑init tras N recargas
F12 | P2 | addon/scripts/5_Mission/MCPBridge.c:4109 | transporte | EncodeQueryValue convierte a ASCII sin +256, así que cualquier byte 0x80‑0xFF (clave o inst no‑ASCII) se manda como '?' en ambos puentes | EVID: asciiCode = character.ToAscii(); | FIX: añadir +256 a código negativo como hace :2479
F13 | P2 | addon/scripts/4_World/MCP_CarScript.c:796 | coste | OnInput modded añade Capture y dos escrituras a todo coche local cada tick aunque no haya control ni traza activa | EVID: MCPVehicleTrace.Capture(this, dt); | FIX: early‑out por bandera global
F14 | P2 | addon/scripts/5_Mission/MCPClientBridge.c:4320 | duplicación | los tags de log, tokens de cola y tipos de callback por lado son el 23 % que una base no puede deduplicar sin hooks | EVID: Log("client result post error=" + errorCode); | FIX: LogTag() y constantes por lado en base
F15 | P3 | addon/scripts/5_Mission/MCPClientBridge.c:663 | transporte | un cambio de url queda sin efecto hasta re‑init porque el puente cachea RestContext y la URL en TryInit | EVID: // A changed url is deliberately NOT adopted | FIX: reintento de re‑init cuando cambie la url

## LO QUE NO PUDE VERIFICAR
- Comportamiento real de `RestApi`/`RestContext` (`reset()`, `GET`/`POST` sobre contexto compartido, si un GET abandonado bloquea POSTs) — en `restapi.c`, no incluido.
- Si el daemon parsea los tags `"[MCP-CLIENT]"` vs `"[DayZ-MCP]"` o las frases intermedias `"client …"` que la propuesta E1 cambia: sin ficheros de test en el material.
- Internos de `MCPDialogController` (`EnsureHost/Open/Tick/FinishDisconnected/GetLastHostError`): la robustez de diálogo está deducida solo de los sitios de llamada.
- Semántica de `FreeDebugCamera.GetInstance/SetActive` y fallos de `g_Game.CreateObject("staticcamera")` — API de motor, no en material [VERIFICAR-API].
- Si `CarScript.OnInput` corre también para vehículos con IA en el cliente (alcance real del coste del override) — comportamiento de motor no verificable aquí.
- Cifras de µs/frame o de memoria: requerirían perfilado; no medibles en revisión de solo lectura.
- Reconteo de las 30 homónimas y del 23 %: tomo los HECHOS VERIFICADOS; `Get` la trato como método y `GetGame`/`Print` como globales del motor (ausentes como definiciones), si el receptor contó call‑sites el mapeo podría variar.
- Si la ausencia de guardia de reentrada en el `Shutdown` del servidor llega a dispararse (llamadas a su destructor no visibles).
- Si activar el watchdog en el servidor es seguro con su modelo de contexto único.

## ¿Qué puede estar mal en la premisa de este encargo?
- Enforce no tiene herencia múltiple, plantillas ni estáticos virtuales: los dos puentes guardan su `static ref MCP<X>Bridge m_Instance` de tipo concreto y un `Get()`/`ShutdownInstance()` que hacen `new` del concreto. Una base NO puede unificar esos 3 puntos (singleton + clases de callback); solo arrastra métodos de instancia y estado no‑concreto.
- Por tanto el 23 % que es "mismo cuerpo, otro literal de clase/etiqueta" (tag de log, token de cola, tipo de callback) NO desaparece al extraer: se reubica en hooks virtuales, añadiendo una indirecta por llamada en lugar de deduplicar. La premisa "extraer base → quitar 23 %" sobrepromete para esa parte.
- Una base definida una vez debe estar visible a ambos paquetes de script (cliente/servidor); el orden de carga no está en el material, así que quizá haya que colocarla en un fichero compartido que los dos vean — supuesto sin verificar.
- Métrica engañosa: lo 23 % "literal" son sobre todo helpers pequeños y estables (`EncodeQueryValue`, `GetPollVersion`, `VectorToArray`), mientras que los que de verdad divergen y más valdría factorizar (`Dispatch`, `StartPoll`, `PostResult`, `OnTick`) NO son deduplicables sin una interfaz de host polimórfica. Invertir esfuerzo en el 23 % apunta a la métrica equivocada.
- El riesgo central de la extracción: los nombres homónimos ocultan semánticas que YA divergieron (`IsFiniteFloat` stricto vs laxo, watchdog y log de caída presentes en un lado solo). Un `MCPBridgeBase` "ingenuo" que elija una copia cambiaría en silencio el comportamiento del otro lado.

GATE NO CORRIDO: revisión por API sin herramientas