# Plan fusionado — `ui_dialog`: interfaces tipo en DayZ-MCP

Council de tres lanes ciegas (Grok 4.6, subagente Fable, ChatGPT Pro) sobre el mismo brief,
más una segunda ronda a ChatGPT con dos hechos que no podía leer. Arbitrado contra el árbol:
toda cita `path:line` de este documento se abrió en disco.

Estado: **plan aprobado por el arbitraje, sin implementar**. No toca código todavía.

---

## 1. Lo que las tres lanes decidieron por separado — cerrado, no se relitiga

Ocho coincidencias independientes. Ninguna lane vio a las otras.

| Decisión | Grok | Fable | ChatGPT |
|---|:--:|:--:|:--:|
| El **agente** consume la respuesta por MCP; nada de RPC cliente→servidor ahora | ✓ | ✓ | ✓ |
| **Una sola tool** con modos, no tres | ✓ | ✓ | ✓ |
| El aviso auto-cierre **no se construye**: ya es `notify_players` | ✓ | ✓ | ✓ |
| El futuro producto de juego se **contiene tras una costura**, no se paga | ✓ | ✓ | ✓ |
| **Un layout fijo**, pre-creado una vez y oculto; filas pre-creadas | ✓ | ✓ | ✓ |
| `CreateWidgets` por apertura: **descartado** | ✓ | ✓ | ✓ |
| Segundo diálogo → **`busy`**, no cola | ✓ | — | ✓ |
| `ui_click` **no** es criterio de aceptación | ✓ | ✓ | ✓ |

Sobre el cacheo de `.layout`, que sigue sin verificar, la formulación de ChatGPT es la que
manda: **cargarlo una sola vez elimina por diseño la dependencia de ese comportamiento
desconocido.** No hay que resolver la incógnita; hay que dejar de depender de ella.

`notify_players` no se toca ni se duplica: es un toast vanilla unidireccional por `ScriptRPC`
(`DayZ_MCP\scripts\5_Mission\MCPBridge.c:587-616` →
`scripts\3_Game\client\notifications\notificationsystem.c:141-150`). La plantilla nueva solo
aporta valor cuando hace falta **saber** que el jugador respondió.

---

## 2. Las dos divergencias, y cómo se resolvieron

### 2.1 Qué dibuja el diálogo → **host pre-creado y oculto**, no `UIScriptedMenu`

Fable proponía heredar de `UIScriptedMenu` para obtener foco y cursor gratis:
`OnShow()` → `LockControls()` → `ChangeGameFocus(1, INPUT_DEVICE_MOUSE)` + `ShowUICursor(true)`
(`scripts\3_Game\tools\uiscriptedmenu.c:173-181` y `:80-102`). Verificado, y es cierto.

Pero esa ruta queda dominada, por un motivo incondicional: **el contrato de `UIScriptedMenu`
es cargar el layout en cada instanciación.** Tres menús vanilla, el mismo patrón en `Init()`:

- `scripts\5_Mission\gui\bookmenu.c:13`
- `scripts\5_Mission\gui\cameratools\cameratoolsmenu.c:141`
- `scripts\5_Mission\gui\chat\chatinputmenu.c:16`

todos `layoutRoot = g_Game.GetWorkspace().CreateWidgets("gui/layouts/….layout");`

Luego instanciar el menú por apertura devuelve el `CreateWidgets` por apertura que las tres
lanes rechazaron. Y la salida obvia —heredar y pre-crearlo oculto— anula el beneficio:
`LockControls()` arranca con `if (IsCreatedHidden()) return;` (`uiscriptedmenu.c:83`, ídem
`:107`). Ese guard va bajo `#ifdef FEATURE_CURSOR` y **no está verificado si ese define está
activo en esta build**; da igual, el primer cuerno basta solo.

**Decisión:** host propio pre-creado con `CreateWidgets` una vez por sesión de misión,
mantenido oculto, destruido con `Unlink()` (`scripts\1_Core\proto\EnWidgets.c:173`) en el
teardown. El bloqueo de input se hace a mano: `ChangeGameFocus(1)` + `ShowUICursor(true)` +
**`PlayerControlDisable(INPUT_EXCLUDE_ALL)`**.

Ese tercer elemento no es opcional: `LockControls` **no** lo hace, y el ATM —código en
producción del mismo autor— lo consideró necesario en su apertura. La forma exacta, leída en
`LFPowerGrid\scripts\4_World\LFPG_BTCAtmView.c:1259-1266`, tiene tres detalles que conviene
copiar y no reinventar: va por `g_Game.GetMission().PlayerControlDisable(INPUT_EXCLUDE_ALL)`,
está guardada por `#ifndef SERVER`, y deja un flag (`m_ControlsLocked`) para poder deshacerlo
**simétricamente** al cerrar. Es evidencia empírica de que cambiar el foco no basta para
impedir que el jugador siga andando, y el flag es lo que evita desbloquear input que no
bloqueaste tú.

**Nada de Dabs.** El ATM hereda de su `ScriptView` (`LFPG_BTCAtmView.c:148-156`,
`GetLayoutFile` / `GetControllerType`), pero copiar esa herencia añadiría `DF_Scripts` como
dependencia a un mod publicado que hoy solo pide `DZ_Data` (`DayZ_MCP\config.cpp:8`). El ATM
es referencia de lectura, no plantilla de herencia.

### 2.2 Cómo espera la llamada MCP → **mecanismo existente, sin protocolo nuevo en Enforce**

Aquí las lanes chocaron de frente y el árbol decidió.

**El problema, que solo Grok vio** (y que ChatGPT no podía ver, porque es Python): todos los
verbos actuales envuelven la llamada al puente en el lock global del broker
(`async with runtime.tool_lock: return await runtime.call_bridge(...)`). Un diálogo que espera
a un humano 60 s, escrito en ese estilo, **congela el daemon multi-sesión durante ese minuto**.
El propio código ya lo dice, en el docstring de la única excepción existente
(`DayZ_MCP_dev\tools\dayz_mcp\server.py:1123-1128`):

> *"This is the only MCP entry that must not wrap its whole body in `runtime.tool_lock`. The
> daemon is a multi-session broker: holding that lock across `await asyncio.sleep` would stall
> every other tool for the full wait. Each probe takes the lock; the sleep stays outside it."*

**La propuesta de ChatGPT** fue partir el protocolo del puente en `open` / `poll` / `cancel`
bajo un mismo nombre de comando, para que ninguna llamada al puente durase más que un
instante. Su justificación era buena: no dar por supuesto que `call_bridge` admite varias
peticiones en vuelo sin demostrar cinco invariantes.

**El árbol dice que ese trabajo ya está hecho, en el lado gratis:**

- Los resultados se guardan **por id de comando**: `self._results[command_id]`
  (`loopback.py:1200`, y `take_result(command_id, remove=False)` en `:1226`). No hay slot
  único compartido.
- `wait_for_result` **ya es un bucle de sondeo por id**: `take_result(command_id, remove=True)`
  → `await asyncio.sleep(POLL_INTERVAL_S)` → repetir hasta el deadline
  (`server.py:301-315`).
- `call_bridge` (`server.py:273-283`) es exactamente *encolar y obtener un id* + *sondear ese
  id*, y **no toma el lock**: lo toman sus llamantes.

O sea que `open`/`poll` ya existe, en Python. Cuatro de los cinco invariantes de ChatGPT se
cumplen por construcción (correlación por id, sin slot compartido, asyncio de un solo hilo sin
futures compartidas, y el id solo lo conoce quien abrió). Construirlo otra vez en Enforce
pagaría el lado **caro** —rebuild de PBO y sesión in-game— por algo ya resuelto en el lado
barato.

**Decisión:** el puente usa el mecanismo diferido que ya existe —`postNow = false` + job con
`deadline_s` en `m_JobRunner`, el patrón de `camera_set` (`MCPClientBridge.c:600-613`) y
`drive_probe_client` (`:669-684`), con el `if (postNow)` en `:577-580`—. **Cero protocolo
nuevo en Enforce.**

El cambio es **solo Python**: `ui_dialog` no envuelve la espera en `tool_lock`. Sigue la forma
de `execute_wait_for`: cada sondeo toma el lock, el `sleep` se queda fuera. Eso exige una
variante del bucle de espera que adquiera el lock por sondeo en vez de sostenerlo entero — un
cambio local, testeable offline, sin tocar el mod.

**Consecuencia documental:** ese *"the only MCP entry"* del docstring pasará a ser falso. Hay
que corregirlo en el mismo commit. Es exactamente la clase de afirmación caducada dentro de un
comentario que ya mordió dos veces en este proyecto.

---

## 3. Diseño

### 3.1 Contrato

Un solo comando, **`ui_dialog`**, con el nombre idéntico en los tres sitios: whitelist
`CLIENT_COMMANDS` en `loopback.py`, la `@app.tool` en `server.py`, y el if/else del puente.
Tres modos:

| `kind` | Uso | Resultado |
|---|---|---|
| `acknowledge` | aviso modal que exige OK | `dismissed_by: "ok"` |
| `confirm` | pregunta sí/no | `choice: "yes"` / `"no"` |
| `form` | 1..N campos de texto | array ordenado `{id, value}` |

Array ordenado y no mapa: conserva el orden declarado, permite detectar ids duplicados en la
validación, y no depende de cómo el serializador de Enforce trate los mapas. Python puede
presentarlo como diccionario al agente.

**Alcance v1: el cliente local donde corre el puente.** Nada de `target_player` ni selección
de jugadores remotos — eso convierte el producto en el otro y exige transporte servidor→cliente.

**Todo campo nuevo se declara en `MCPArgs`**, incluidos los miembros de cada elemento de
`fields`: las claves no declaradas se descartan **en silencio** al deserializar. Consecuencia
directa para las pruebas, en §5.

### 3.2 Estados terminales

`completed` · `cancelled` · `timed_out` · `disconnected` · `rejected` (con `reason: "busy"`).

Tres reglas que vienen de ChatGPT y que conviene no ablandar:

1. **Cancelar nunca es "No".** Cerrar una confirmación devuelve `cancelled`, no `choice:"no"`.
   Confundirlos destruye información que el agente necesita para decidir.
2. **Nada de respuestas parciales.** Cancelar o vencer un formulario no devuelve lo escrito:
   no hubo consentimiento de envío, y devolverlo sería ambiguo.
3. **`timed_out` ≠ error de transporte.** El primero lo produce el job del cliente cuando el
   jugador no contesta. El segundo es que Python no consiguió resultado. No se falsifica uno
   como el otro, porque en el segundo caso no sabemos si el jugador respondió.

### 3.3 Finalización única — guarda de reentrada, no de concurrencia

Enforce corre en un solo hilo (el tick del cliente): `OnClick` y el vencimiento del job **no**
se ejecutan en paralelo. ChatGPT retiró por eso su caracterización de "carrera" y su estado
`COMPLETING`; queda:

`IDLE → OPEN → TERMINAL → IDLE`

`TryFinish` comprueba que sigue en `OPEN`, pasa a `TERMINAL` **antes** de ocultar widgets,
limpiar campos o guardar el resultado, y no hace nada si ya no lo estaba. Protege de dos
llamadas **ordenadas** a la lógica terminal, que sí existen: un click y luego el barrido del
runner en el mismo tick; un timeout y luego un evento de click ya encolado; ocultar o cambiar
el foco disparando otro callback síncrono; el teardown cerrando algo ya terminado; y dos rutas
de cierre distintas (botón Cancelar y cierre general) llegando a la misma función.

Dos detalles suyos que valen y que no son obvios:

- **`TryFinish` no elimina el job de la colección que el runner está recorriendo.** Lo marca
  terminal y guarda el resultado; el runner compacta después de terminar la iteración. Es
  modificación-durante-iteración, no concurrencia.
- **Si `now >= deadline`, gana el timeout**, aunque el handler de click se procese antes que el
  barrido. Así el resultado no depende del orden de dispatch dentro del frame.
  *Contrapartida asumida:* un click humano hecho a tiempo pero procesado un frame tarde se
  reporta como `timed_out`. Ventana ~16 ms; se acepta a cambio de determinismo, que para un
  arnés de automatización vale más.

### 3.4 La costura hacia el producto de juego

El controlador consume una especificación de diálogo y produce un resultado **sin conocer
MCP**. Entrega su desenlace a un sink (~10 líneas de interfaz, forma de Fable). El sink v1 es
el job del puente. Un futuro producto de juego añade un sink RPC y su validación autoritativa
en el servidor, sin tocar layout, controlador ni contrato.

Hoy no hay nada de eso construido: **cero hits de `ScriptRPC`/`OnRPC` en `DayZ_MCP\scripts\`**
(medido). Eso es exactamente lo que no se va a pagar ahora.

---

## 4. Fases

| # | Trabajo | Criterio de aceptación medible | ¿Rebuild? |
|---|---|---|---|
| 0 | Contrato cerrado: nombre, modos, límites, estados, alcance local, relación con `notify_players` | Existe la spec con ejemplo de los tres modos y de los cinco estados terminales; ningún nombre alternativo abierto | No |
| 1 | Python: tool, validación estricta, whitelist, y la espera **fuera** del `tool_lock` + corregir el docstring de `execute_wait_for` | Claves desconocidas rechazadas; `N+1` campos rechazado sin abrir UI; **con un diálogo de 60 s abierto, una segunda sesión ejecuta otra tool y recibe resultado antes de que el diálogo termine**; ningún `sleep` del bucle corre poseyendo el lock | No |
| 2 | Lote **único** Enforce + layout: host oculto, controlador, `TryFinish`, dispatch, job diferido, args, limpieza, teardown | Revisión estática: cero BOM, cero ternarios, cero locales sin tipo, cero condiciones booleanas partidas, cero `new` en rutas por tick; correspondencia 1:1 entre contrato y campos declarados en `MCPArgs` | **Sí — una sola candidata** |
| 3 | Sesión in-game de aceptación, matriz completa sin re-empaquetar entre casos | El servidor arranca sin errores de script y pasan los 12 casos de §5 | No adicional |
| 4 | Publicar el contrato: docstring, changelog en `product-spec.md`, regresión de los verbos existentes | Changelog publicado; `ui_tree`/`ui_set_text`/`ui_click`/`action_use`/`notify_players` sin cambio observable | No |

Si la fase 3 saca varios fallos, **se acumulan y se genera una sola segunda candidata**, no un
empaquetado por bug. (Es DZ-R5, que ChatGPT dedujo por su cuenta sin conocerla.)

---

## 5. Matriz de aceptación (fase 3)

1. `ui_dialog` atraviesa whitelist y dispatch sin `not_whitelisted` ni `unknown_command`.
2. **Un valor centinela distinto por cada argumento** aparece en la UI o en el resultado.
   Ninguno se pierde. *Esto existe porque una clave no declarada se descarta en silencio: que
   la UI se abra NO prueba que llegara todo.*
3. `acknowledge` con click humano en OK → exactamente un `completed`.
4. `confirm` → `choice:"yes"` y `choice:"no"` con click humano.
5. Cerrar una confirmación → `cancelled`, **no** `choice:"no"`.
6. `form` de 1 campo devuelve su `id` y valor; de N campos, N respuestas **en el orden declarado**.
7. `N+1` campos → rechazado **sin abrir la UI**.
8. Obligatorio vacío → la UI sigue abierta y muestra el error; la llamada sigue pendiente.
9. El formulario siguiente empieza limpio: sin valores, errores, foco ni paneles heredados.
10. Sin interactuar → `timed_out`, UI oculta, input desbloqueado.
11. Segundo diálogo con uno abierto → `rejected/busy` **rápido**, sin bloquear hasta el timeout
    del primero, y sin alterar el primero.
12. Texto con acentos, `ñ`, comillas, barras y saltos de línea hace round-trip sin romper el JSON.

**Alcanzabilidad — el gate real.** Para cada botón: abrir, leer `ui_tree`, comprobar
`visible`, `visible_hierarchy`, `disabled=false`, `ignore_pointer=false`, ancho y alto > 0 y
rectángulo dentro de pantalla; **y después pulsar físicamente con el ratón en el centro de ese
rectángulo**. `ui_click` se usa aparte, solo para confirmar el cableado del handler, y **no
cuenta como aceptación**: invoca el handler directamente y se salta hit-testing, orden Z e
`IGNOREPOINTER`. Repetir en dos resoluciones.

---

## 6. Riesgos y desconocidos

**Verificado en el árbol:**
- Pérdida silenciosa de argumentos no declarados en `MCPArgs` → mitigado por el caso 2.
- El lock global del broker → mitigado por la fase 1, con criterio medible.
- Coste del gate Enforce: el único compilador real es arrancar el servidor → un solo lote.
- El docstring de `execute_wait_for` quedará desactualizado → se corrige en el mismo commit.
- Estado residual al reutilizar un host oculto → caso 9.

**Sin verificar, y declarado como tal:**
- Si `CreateWidgets` relee el `.layout` o lo cachea. **El diseño no depende de ello** (una sola
  carga por sesión, sin recarga en caliente).
- Si `FEATURE_CURSOR` está definido en esta build. Irrelevante para la decisión tomada.
- Punto exacto del ciclo de misión donde crear y registrar el host, y validez de
  `GetWorkspace()` ahí. **Hay que resolverlo antes de la fase 2**; el ATM da el precedente.
- Si el serializador admite un array de objetos con todos sus miembros declarados como
  argumento de ENTRADA (en resultados sí hay arrays de objetos). **Demostrarlo con una petición
  de dos campos antes de cerrar el DTO.**
- Convivencia de foco/cursor/bloqueo de input con inventario, pausa y otras UIs.

**De producto:** las respuestas son datos para el agente, **no órdenes autoritativas**. No
deben mutar por sí mismas misiones, dinero, inventario ni posición. Y **no loguear las
respuestas completas por defecto**: pueden llevar texto privado escrito por un jugador.

---

## 7. Lo que no se hace

RPC cliente→servidor · jugadores remotos · tres tools públicas · duplicar el aviso automático ·
un layout por pregunta · `CreateWidgets` por apertura · construir la UI programáticamente ·
crear widgets en rutas por tick · cola de diálogos · tratar cancelar/timeout como "No" ·
devolver texto parcial · heredar de Dabs · recarga de layouts en caliente (aparcada:
`fb-20260817-151939-bb31`) · tocar LFPowerGrid · rehacer los tres verbos de UI.
