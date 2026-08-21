# DayZ-MCP — Arquitectura (end-goal)

> **Objetivo:** un servidor MCP que expone DayZ (DayZDiag) como **tools tipadas**, para que un
> agente conduzca el juego y extraiga datos estructurados — server-authoritative, **sin teclas
> SO ni OCR**. (La captura visual es engine-native SI `MakeScreenshot` funciona en el exe diag;
> si no, cae a window-grab del cliente — ver §6 y el caveat T165276.)
> **Base:** 2 workflows de investigación (≈2.5M tokens) + spot-check directo de Claude sobre
> cada API load-bearing (leídas en el source vanilla bajo `<vanilla scripts root>`).
> Generado 2026-06-06 como documento de diseño. **Implementado y en produccion desde
> entonces**: el servidor expone hoy 53 tools y el puente va por `MCP_BRIDGE_VERSION = "8"`.
> Esto queda como el diseño original — util para entender por que las piezas son como son,
> no como descripcion del estado actual. Para eso, `README.md` (superficie de tools) y el
> bloque LIVE-STATE de `HANDOFF.md`.

---

## Resultado del probe in-game (2026-06-06) — `MakeScreenshot` descartado

> **Verificado end-to-end por Claude** (no solo T165276): mod client-side `@MCPTest`
> (`modded MissionGameplay`) cargado en el **exe diag** vía filepatching, player in-game
> (`SurvivorM_Francis`), `MakeScreenshot("$profile:mcpshot.dds")` + default llamados 2× →
> **cero `.dds`** en barrido exhaustivo (`%localappdata%\DayZ`, Documents, profiles cliente/
> server, game dir; ni la carpeta `ScreenShots` se crea). **`MakeScreenshot` es no-op también
> en DayZDiag**, no solo en retail.
> → **Decisión cerrada:** la captura visual del MCP usa **window-grab del cliente renderizado**
> (screen-grab del SO / computer-use, *pasivo* — leer pixeles, no inyectar input). NO afecta al
> control ni a los datos (siguen engine-native); solo a la captura.
> **RenderTarget descartado como alternativa (verificado en source):** `RenderTargetWidget`
> (enwidgets.c:236) es display-only — solo `SetRefresh`/`SetResolutionScale`, **sin readback a
> archivo**; el wrapper `RenderTarget` (rendertarget.c) es `#ifdef GAME_TEMPLATE`. Un barrido del
> source confirma que `MakeScreenshot` (proto.c:142, roto) era la **única** captura-a-disco del
> engine. → **window-grab del cliente renderizado es la única vía visual.**
> **RestApi tampoco (misma raíz):** `RestContext.POST` envía un `string` y `ReadFile`
> (ensystem.c:425) lee bytes raw del disco — el transporte existe; pero **ninguna API de script
> materializa la vista renderizada** como archivo/bytes (`MakeScreenshot` roto, `RenderTargetWidget`
> sin readback). El framebuffer está fuera del alcance del script → por eso el window-grab es
> **externo** (SO), no script. El RestApi sí es la columna ideal para todo lo NO-visual (datos/telemetría/raycast → JSON).
> El probe quedó reutilizable en `MCPTest/` (mod + `mcp-shot-test.ps1`) como
> plantilla de "test client-side autónomo".
>
> **Window-grab CONFIRMADO viable (probe 2026-06-06):** `Graphics.CopyFromScreen` (PowerShell,
> sin grant ni computer-use) capturó el display con contenido real (meanB 65, nbRatio 0.999) con el
> cliente in-game → la captura visual del MCP **es viable**. **Pendiente afinar la selección de
> ventana**: el primer intento usó `MainWindowHandle` y cogió la ventana equivocada (rect 868×517,
> capturó parte del escritorio, no solo el render). Fix: enumerar las top-level windows del proceso
> cliente y elegir la de título "DayZ"/tamaño de juego, **o** `PrintWindow(hwnd)` (caveat: apps
> DirectX a veces devuelven negro con PrintWindow). Mecanismo validado; queda la puntería.

---

## 1. El modelo mental correcto: 3 actores (no 1)

El error tentador es pensar "un peer server-authoritative que hace todo". **No.** El render de
DayZ es client-only, así que hay **tres** actores y el mod-puente cose dos de ellos:

| Actor | Qué es | Qué ejecuta |
|---|---|---|
| **MCP server** | Proceso externo (Python+FastMCP, stdio). Es además el **HTTP server** al que el mod llama. | Traduce tools MCP ↔ comandos; mantiene la sesión; convierte DDS→PNG. |
| **CONTROL peer** | `MissionServer` (server diag) | spawn/seat/drive/world/weather — **sin renderer** |
| **CAPTURE peer** | `DayZDiag_x64` cliente (renderer activo) | cámara + captura + scene-query visual |

Implicación dura: **una build server-only (CI headless) NO puede capturar** — `MakeScreenshot`/
`SetCamera` no producen nada sin renderer. La captura exige un cliente renderizado. Los datos
(spawn, telemetría, raycast) sí salen server-only.

---

## 2. Cimientos verificados (Claude leyó el source esta sesión)

Todas con cita `file:line`. Guard: `—` = unguarded (vanilla); `DIAG` = requiere `DayZDiag_x64 -define=DIAG_DEVELOPER`.

**Llegar a un player controlable** (CONTROL):
- `proto native int Connect(UIScriptedMenu parent, string IpAddress, int port, string password)` — game.c:251 `—`
- `OnClientNewEvent(PlayerIdentity, vector pos, ParamsReadContext)` — missionserver.c:536 `—`
- `CreateCharacter(PlayerIdentity, vector pos, ParamsReadContext, string name)` — missionserver.c:486 `—`
- `InvokeOnConnect(PlayerBase, PlayerIdentity)` — missionserver.c:422 `—` (gate de "controlable")

**Escenario** (CONTROL):
- `CreateObjectEx(string type, vector pos, int iFlags, int iRotation)` — game.c:702 `—`
- `World.SetDate(...)` — usado en tu autospawn_init.c:15 · `World.SetTimeMultiplier(float)` — world.c:19 `—`
- `WeatherPhenomenon` — weather.c:28 `—`

**Vehículo** (CONTROL):
- `StartCommand_Vehicle(Transport, posIdx, seat, bool)` — human.c:1492 `—` (asiento conductor = `(t,0,0)`)
- `Car.SetThrottle/SetSteering/SetBrake/ShiftTo` — car.c:202/196/214/271 `—`
- `IsGettingIn()` / `IsGettingOut()` — human.c:705/706 `—` (predicado de "ya sentado")

**Cámara + captura** (CAPTURE — client-only):
- `SetCameraEx(int cam, const vector mat[4])` — enworld.c:56 `—` (pose por matriz)
- `GetCamera(int cam, out vector mat[4])` — enworld.c:59 `—` (lee pose, conserva roll)
- `MakeScreenshot(string name)` — proto.c:142 `—` ⚠️ **el proto existe pero el runtime se reporta ROTO** ([T165276](https://feedback.bistudio.com/T165276): crea solo carpeta vacía en cliente retail, reproducido hasta 1.19; quizá solo el exe **diag**). **NO asumir funcional** — fallback de captura en §6.
- `SetWidgetWorld(RenderTargetWidget, world, int camera)` — enwidgets.c:705 `—` (render off-screen multi-cam; salida a textura, extracción a archivo también a validar)

**Transporte + serialización + escape**:
- `RestContext.GET(RestCallback, request)` / `POST(RestCallback, request, data)` — restapi.c:103/123 `—` (**async, no bloqueante**)
- `RestContext.GET_now/POST_now` — restapi.c:108/128 `—` (**bloqueante — NO usar en el tick**)
- `class RestCallback : Managed` — restapi.c:50 `—`
- `JsonFileLoader<T>.JsonLoadFile/JsonSaveFile` — jsonfileloader.c:105/134 `—` · `JsonSerializer` — gameplay.c:49 `—`
- `ExecuteEnforceScript(string expr, string mainFnName)` — game.c:776 `—` (send), pero el **resultado** del script-console es `DIAG`
- Tick hook del loop de comandos: `MissionGameplay.TickScheduler(float)` — missiongameplay.c:216 `—`

---

## 3. Arquitectura de transporte

```
Claude ──stdio/JSON-RPC──> MCP server (Python+FastMCP)
                              │  (es también un HTTP server local en 127.0.0.1)
              mod hace HTTP async ↑ pull comandos / push resultados
                              │
   @DayZ_MCP_Bridge (Enforce) ── dispatch en TickScheduler ──> CONTROL APIs (server)
                              └── reenvía a ──────────────────> CAPTURE APIs (client, vía RPC/SyncVar)
```

- **MCP↔Claude:** stdio (JSON-RPC, single-user local, cero red, patrón Blender/Unreal MCP).
- **MCP↔mod:** HTTP vía `RestApi`, pero el lado in-game usa los callbacks **async** `GET()/POST()`
  (restapi.c:103/123), **nunca** `*_now` dentro del tick (son *thread-blocking* — congelarían el
  sim 60 Hz por el RTT). El mod **pull**ea comandos y **push**ea resultados; el MCP server es el
  HTTP endpoint.
- **Fallback:** file-bridge (`OpenFile`/`FGets`/`FPrintln`, ensystem.c) para 100% offline/CI.
  Ojo regla OneDrive race del proyecto: el file-bridge va fuera de OneDrive.

---

## 4. Tool surface (53 tools (+ `exec_enforce` when an allowlist is configured))

El recuento sale de `tools/tests/test_install_mcp.py::PublicToolCountDocsTest`:
`build_app` → `app._tool_manager.list_tools()`, descartando `ui_dialog` del número
público (el README no la nombra; este documento sí). El diseño de 2026-06-06 abajo
era 11 tools / 6 dominios; eso ya no es la superficie instanciada.

`dayz_test_run` no espera a que el juego esté listo para “parecer éxito”: un arranque
que no confirma devuelve `status=failed` y `error_code` (no un campo `error`). El
código concreto viaja en `error_code` (p.ej. `instance_config_missing`); `run_id` no
es señal de éxito. No hace falta que cada conductor reinvente un poll de 120 s.

Mapeadas a las APIs verificadas. 9 unguarded, 2 `DIAG`. (superficie inicial, 2026-06-06)

- **session:** `session_connect` (Connect + OnClientNewEvent) · `session_status` (player listo?) · `session_disconnect`
- **world:** `world_spawn` (CreateObjectEx) · `world_time_set` (SetDate/SetTimeMultiplier) · `world_weather_set` (WeatherPhenomenon)
- **vehicle:** `vehicle_enter` (StartCommand_Vehicle) · `vehicle_drive` (Car.SetThrottle/SetSteering/SetBrake)
- **scene:** `scene_raycast` (DayZPhysics.RaycastRVProxy + GetCrosshairObject) — "ver" sin pixeles
- **camera+capture:** `camera_set`/`camera_get` (SetCameraEx/GetCamera) · `capture_screenshot` (MakeScreenshot **o** window-grab fallback → PNG→base64 ImageContent — ver §6)
- **exec (breakglass, `DIAG`):** `exec_enforce` (ExecuteEnforceScript) — **solo** para lo no mapeado, con whitelist
- **telemetry:** `telemetry_read` (XML/CSV del AutoTestFixture / JSON-lines)

Patrón de diseño (de prior-art Blender/Unreal MCP): `get_property`/`set_property` genéricos donde
quepa, `status()` antes de mutar, errores de negocio vía MCP `isError` (no excepción de protocolo).

### 4.1 Familia UI (2026-08-19; runs `08343f0c`, `fdf07db7`)

Verbos: `ui_tree`, `ui_set_text`, `ui_click`, `ui_dialog`, `ui_reload_layout`, `ui_focus`.
`ui_focus` da el foco de teclado a un widget por nombre y responde `ok` solo si
`GetFocus()` devuelve el que se pidio; un widget sin inputs contesta `found` con
`focus_not_taken`. Existe porque el relleno del estado Focus de
`ButtonWidget/EmptyHighlight` no se puede observar de ninguna otra forma sin
un humano al teclado, y `ui_click` no da foco: llama `OnClick` directamente.
Medido in-game, no re-derivado.

`ui_reload_layout` recarga un `.layout` desde `$profile:` en el cliente vivo y
devuelve los rects del motor. El fichero se relee en **cada** llamada. El prefijo
de addon lo sirve el PBO y **solo** el PBO. `FileExist` guarda contra un CTD
dentro de `CreateWidgets`. Un segundo `CreateWidgets` apila en vez de reemplazar.

---

## 5. Readiness protocol (resuelve "¿ya está listo?")

MCP es petición/respuesta; DayZ es tick-based y asíncrono. Cada comando devuelve un **job-id** y el
modelo consulta hasta que el **predicado de completitud** se cumple (el mod los evalúa en el tick):

| Comando | Predicado de "listo" |
|---|---|
| spawn player | entity existe **AND** `InvokeOnConnect` disparado |
| seat | `IsGettingIn()==false` + 1-2 ticks de asentamiento |
| drive | delta de posición/`GetSpeedometer()` > umbral |
| screenshot | archivo existe **AND** size>0 **AND** `GetCamera` == pose comandada |

Sin esto, el agente hace blind-poll o `sleep` fijo → frágil. El POC debe traer ya este protocolo.

---

## 6. Pipeline visual (la parte incierta — ver caveat MakeScreenshot)

1. **Condicionar** escena: `SetDate` + `SetTimeMultiplier(0)` + weather despejado → frame reproducible.
   ⚠️ `SetTimeMultiplier(0)` **congela TODA** la simulación (animaciones incluidas). No congelar
   mientras se espera una animación (p.ej. get-in) o se deadlockea: condicionar **después** de sentar.
2. **Encuadrar:** `SetCameraEx(pose)` por ángulo (orbital), `GetCamera` para medir/registrar.
3. **Capturar — INCIERTO:** intentar `MakeScreenshot` → **DDS** (salida observada en
   `%localappdata%\DayZ\ScreenShots\`, nombre con timestamp, no determinista). ⚠️ **Reportado
   no-funcional** en cliente retail (crea la carpeta vacía, [T165276](https://feedback.bistudio.com/T165276));
   incierto en el exe **diag** (un reporte sugiere que solo ahí funcionaba). **Fallback fiable si
   falla: captura de ventana externa** del cliente renderizado (screen-grab nativo / computer-use) —
   reintroduce dependencia de ventana, pero NO de teclas SO. **El POC visual decide cuál sirve; no
   construir la fase visual sobre `MakeScreenshot` hasta confirmarlo in-game.**
4. **Entregar:** si la fuente es DDS → conversión **out-of-band** en el host (Pillow/ImageMagick):
   DDS→PNG, downscale <1 MB (límite ImageContent), base64 → MCP `ImageContent`. Si es window-grab,
   ya es PNG. No hay encoder PNG in-engine.
5. **Multi-cam off-screen** (a validar): `SetWidgetWorld` + `RenderTargetWidget` para varias cámaras
   sin mover la del player — pero la extracción de esa textura a archivo también está sin verificar.

Riesgo de **stale-frame**: el render puede ir 1-2 frames por detrás del `SetCameraEx`; gate por
estabilidad antes de capturar o la imagen "medida" miente.

### 6.1 Resolución entregable y zoom (upgrade 2026-06-28)

El techo de ~25k tokens por respuesta de tool (CONFLICT-1, Claude Code #9152) limita el **payload
base64**, no los píxeles. Calibrado offline sobre un grab real (`fase3-evidence-subject.png`,
1302×776 nativo):

| Vía | Ancho útil dentro de 25k tokens |
|---|---|
| PNG (legacy) | **199 px** ← por esto las capturas eran ilegibles |
| JPEG q82 (default nuevo) | **588 px** |
| JPEG q70 | ~704 px |

Cuatro palancas, todas dentro del mismo presupuesto:

1. **JPEG en vez de PNG** (default). Un frame foto-realista comprime ~5-8× mejor. `image/jpeg`
   es válido como ImageContent; PNG sigue seleccionable (`fmt="png"`).
2. **`crop`** (`"center"`, `"center:0.4"`, o bbox normalizada `"l,t,r,b"`) — recorta ANTES del
   downscale a presupuesto → gasta todo el presupuesto en el sujeto (zoom de software). Fail-open:
   spec inválida devuelve el frame entero, nunca se pierde la captura.
3. **Zoom óptico in-game** vía `camera_set(fov=...)`. **Patrón de dos disparos**: wide (`fov≈70`)
   para verdict de encuadre/orientación + tight (`fov≈25-35` o acercar `cam_pos`) para detalle.
   El tight llena el frame con el sujeto a resolución nativa antes de cualquier downscale →
   detalle sin pérdida por compresión. Mejor que `crop` cuando el sujeto es pequeño en el wide.
4. **Canal dual (`save_fullres=True`)** — escribe el frame full-res a disco y devuelve su ruta en
   un bloque de texto JSON junto al thumbnail inline. El cap de 25k es solo sobre la salida del
   tool; el agente abre el JPEG full-res por el canal de lectura de imagen normal → **detalle
   ilimitado para el verdict fino**, thumbnail minúsculo para el vistazo rápido. Destino:
   `save_dir` > `$DAYZ_MCP_CAPTURE_DIR` > `<temp>/dayz_mcp_captures` (ruta absoluta).

Default sin `save_fullres` devuelve un único `Image` (ahora JPEG) — backward-compatible.

---

## 7. Seguridad — fail-closed desde el día 1 (no fase final)

`RestApi` habla con URLs arbitrarias **sin auth**, y el send-path de `ExecuteEnforceScript`
(playerbase.c:5698) ejecuta Enforce arbitrario server-side. Combinado = agujero de
server-takeover. Por R6 (fail-closed), de salida:

- **Bind 127.0.0.1** (nunca 0.0.0.0); fallar si no se puede constatar el binding.
- **API-key/token** por request (header `Authorization`); rechazar si falta.
- **Whitelist de comandos**; `exec_enforce` solo con allowlist de símbolos, tratado como breakglass
  auditado, no como tool universal (invita al modelo a saltarse las tools tipadas → mutaciones no auditables).
- Pinear un **snapshot de `ERPCs`** + hash de versión DayZ y validar en el handshake (los opcodes RPC
  son posicionales; un parche vanilla que inserte un enum desplaza todos y misrutea en silencio).

---

## 8. Plan de build por fases

| Fase | Entrega | Esfuerzo | Desbloquea |
|---|---|---|---|
| **0. POC** ⭐ | round-trip **async no bloqueante** + readiness ACK, server-only: `query_player_state` → mod despacha por callback `RestApi` desde TickScheduler → devuelve posición con correlation-id | ~1 sem | de-risca lo crítico: transporte sin bloquear el tick + protocolo de readiness |
| 1. Control | `world_spawn` + `vehicle_enter` (con `IsGettingIn`) + `vehicle_drive` | med | escenario + conducir sin input SO |
| 2. Observación | `scene_raycast` + `telemetry_read` (verdict sin pixeles) | low | tests headless |
| 3. Visual | cámara + `capture_screenshot` — **primero resolver MakeScreenshot vs window-grab** (ver §6); necesita cliente renderizado | med→alto | "ver" multi-ángulo |
| 4. MCP completo | tool surface entera + security + packaging/install | med | el end-goal |

**El POC (fase 0) es el de mayor leverage**: si el round-trip async + readiness funciona sin
estancar el sim, el resto es ingeniería sobre APIs ya verificadas. Es el primer in-game test real.
**Mover la decisión MakeScreenshot/window-grab a un mini-test temprano** (barato: una llamada +
mirar la carpeta) para no diseñar la fase 3 sobre una API rota.

---

## 9. Riesgos y límites honestos

- **`MakeScreenshot` reportado roto** ([T165276](https://feedback.bistudio.com/T165276)): carpeta vacía en retail → la captura puede tener que caer a **window-grab externo** (rompe el "sin captura de ventana"; control/datos siguen native). Confirmar en un mini-test antes de la fase 3.
- **Bloqueo del loop:** `*_now` en el tick congela 60 Hz por el RTT → usar callbacks async. (decidido)
- **Headless no captura:** server-only no rinde → la fase visual exige cliente con renderer.
- **Time-freeze deadlock:** `SetTimeMultiplier(0)` para animaciones pendientes → ordenar conditioning.
- **DIAG_DEVELOPER asimétrico:** el *resultado* de `exec_enforce` (script-console) es `#ifdef DIAG_DEVELOPER` (scriptconsoleenfscripttab.c:208) aunque el send sea `#ifdef DEVELOPER` → el harness exige build `DIAG_DEVELOPER`.
- **Stale-frame** en screenshots (gate de estabilidad).
- **Version drift** de opcodes RPC (pinear + validar en handshake).
- **BattlEye / filePatching** en loops de hot-reload (0x00020005) — ya cubierto por tu infra diag.
- **Concurrencia:** una instancia DayZDiag = un dueño; sin lock, dos tool-calls corrompen estado/cámara.

---

## 10. Nivel de verificación

- **Verificado por Claude** (leído en source esta sesión, el proto existe): TODAS las APIs de §2 — Connect, OnClientNewEvent/CreateCharacter/InvokeOnConnect, CreateObjectEx, SetTimeMultiplier, StartCommand_Vehicle, Car setters, IsGettingIn, SetCameraEx/GetCamera, SetWidgetWorld, RestApi GET/POST async y `*_now`, RestCallback, JsonFileLoader/JsonSerializer, ExecuteEnforceScript, TickScheduler. **`MakeScreenshot`: el proto existe (proto.c:142) pero su RUNTIME se reporta roto — ver abajo.**
- **De W2 (subagentes) + prior-art web, NO re-verificado por mí:** los patrones MCP (Blender/Unreal/Unity/Godot), límites de ImageContent (<1 MB), y los comportamientos runtime (latencia por tick, frame-lag de captura, `WeatherPhenomenon.Set`).
- **Reportado roto por fuente externa (verificado por el usuario):** `MakeScreenshot` — feedback tracker [T165276](https://feedback.bistudio.com/T165276) (carpeta vacía en retail hasta 1.19; posible solo en diag). El diseño lleva fallback window-grab.
- **A confirmar in-game (no verificable offline):** latencia del round-trip async; que el cliente diag spawnee controlable vía `Connect` programático; que la captura refleje la pose sin lag; y **si `MakeScreenshot` funciona en el exe diag** (mini-test temprano).

El primer gate real es el **POC (fase 0)**; la incógnita `MakeScreenshot` se resuelve con un mini-test aparte antes de la fase 3.
