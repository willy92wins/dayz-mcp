Voy a leer el brief del consejo y las skills que apliquen, y luego responderé en las seis secciones que pide, sin escribir ficheros.El brief pide juicio de diseño con citas verificadas. Voy a abrir primero los informes que ya refutan hot reload y auto-suicidio, y después el código real del padre/hijo y del daemon.Siguiente: el arranque padre/hijo, el daemon y el SDK MCP, para ver si ya hay una frontera reciclable o si el transporte sigue atado al proceso que hay que recargar.El `--client` no spawnea un hijo en `__main__.py`; voy a ver si lo hace FastMCP, el launcher del anfitrión o el propio daemon.## 1. Recomendación

**(A)** — un supervisor estable dueño del stdio del anfitrión y un trabajador `--client` reciclable que la IA puede matar y volver a lanzar; el host no reabre nada y no hay hot-reload del registro FastMCP.

## 2. Por qué esa y no las otras

**Hot-reload del registro FastMCP (B in-process).** Sigue refutado. Los validadores de schema capturan el `call_fn_with_arg_validation` previo (`server.py:1935-1943`, `:1955-1963`); `_client_runtime()` hace `isinstance(runtime, ClientRuntime)` (`:3330-3333`); `ClientRuntime` crea `session_id` nuevo y un `ControlClient` con lease/ticket/operation (`:1210-1227`, `control_client.py:161-166`); `dayz_test_run` arma un heartbeat de caja en una task local (`server.py:3579-3581`). Nada de eso es hoja. No ataco esa refutación.

**Que el proceso stdio se suicide para que el anfitrión lo reabra.** Sigue refutado, y la razón dura se sostiene: el handler retorna en `lowlevel/server.py:770`, `message.respond` va en `:800`, y el write+flush lo hace **otro** task (`stdio.py:75-81`). El SDK admite requests concurrentes (`lowlevel/server.py:678-684`). `_release_and_exit` documenta que el peer ya no está y hace `os._exit(0)` (`server.py:5389-5397`); no es un cierre de una llamada viva. No hay en el SDK instalado (`mcp==1.27.2`) ningún mensaje servidor→cliente de restart/reconnect; `notifications/tools/list_changed` solo dice que cambió **la lista** (`types.py:1372-1376`) y `ServerSession.send_tool_list_changed` (`session.py:477-479`) no recarga bytecode. **[SUPUESTO]** Claude/Cursor/Codex no reponen un stdio muerto a mitad de chat; aunque alguno lo hiciera, sería un accidente de anfitrión, no un verbo de producto.

**El padre/hijo ya medido no es esa media solución.** `--client` no spawnea un trabajador MCP: `__main__.py` llama `run()`, y `run()` en modo client construye la app y hace `app.run(transport="stdio")` (`server.py:5400-5437`) sin watchdog de padre (el comentario en `:5418-5422` lo dice: el client no posee puerto). FastMCP ata stdin/stdout **de este proceso** (`fastmcp/server.py:753-760` → `stdio.py:39-50`). El par “mismo comando, padre→hijo” es el **redirector del venv** (`Scripts\python.exe` lanza `C:\Python314\python.exe`): el árbol ya lo trata como capa a saltar, no como supervisor (`orphan_guard.py:314-319`; `identity_migration.py:605-609`; `daemon.py:1609-1613`; `pyvenv.cfg` apunta a `C:\Python314`). Ese padre espera al hijo; no parsea JSON-RPC. Matar al hijo tumba el stdio. No hay frontera reciclable ya construida.

**Mover la ejecución de tools detrás del daemon.** El daemon ya es el proceso compartido de runs (`daemon.py:413`, `:520`, `:598`), ya vigila frescura (`:650-671`, `:747-755`) y ya puede reiniciarse en vacío con recovery (`:568-581`). Reciclarlo aplica código **a las seis sesiones a la vez** y choca con la caja FIFO única. Además las tools viven en el proceso `--client` (`build_app` registra todo ahí; `capture_screenshot` es local, `:4664`; playbooks también). Un bug en `server.py` no se aplica reiniciando `:8765`. Es el peor radio de explosión para el caso que importa.

**Reestructurar el árbol a hojas recargables.** `playbook_reload` ya demuestra que una hoja con recibo de bytes funciona (`playbook_tool.py:74-82`, `:85-129`; registro en `server.py:5338-5351`). Casi nada más es hoja: el adaptador FastMCP, los patches de schema y el `ClientRuntime` no lo son. Generalizar ese patrón es una reescritura permanente, no un arreglo del “apliqué un parche al servidor que estoy usando”.

**Afordancias de protocolo/anfitrión.** No resuelven aplicar código. `list_changed` refresca el catálogo. El remedio publicado sigue siendo `reopen_mcp_client` (`server_freshness.py:24`).

## 3. Coste y riesgo

**Tocar.** Un proceso padre **nuevo** (no el redirector del venv) que:
- posee stdin/stdout del host y reenvía JSON-RPC;
- lanza un hijo que corre el FastMCP de hoy, pero sobre un pipe (`stdio_server` ya acepta streams inyectados, `stdio.py:34-50`; el SDK ya corre sin stdio de proceso en tests: `create_connected_server_and_client_session` en `test_server_freshness.py:340-346`);
- al reciclar: deja de admitir, espera o cancela lo en vuelo **en el padre** (el flush al host queda en el padre: eso ataca la razón 2 de RONDA3 sin ignorarla), spawnea hijo nuevo, **repite `initialize`** hacia el hijo (el host ya inicializó), y si cambió el catálogo emite `notifications/tools/list_changed`.
- Un verbo MCP (`mcp_recycle_worker` o similar) que solo recicla **ese** hijo, y se niega con playbook activo, `dayz_test_run`/heartbeat de caja, o requests en vuelo — el mismo estilo de admisión que `playbook_reload`.

**Identidad de sesión.** El lease no casa por `session_id` suelto: `_validate_token_locked` exige `self._active.client == client` (`session_coordination.py:3430-3435`) y `ClientIdentity` incluye `pid`, `ppid`, `started_at_utc` y `session_id` (`:57-63`). Un hijo con PID nuevo pierde el lease. El padre tiene que **congelar** esa identidad (PID del supervisor, `session_id` y `started_at_utc` de arranque) e inyectarla al trabajador. Si no, el reciclo es un `session_release` encubierto.

**Qué se rompe si se hace mal.** Llamadas concurrentes a mitad de reciclo (el SDK las admite). Heartbeat de caja cortado (`BOX_CLAIM_TTL_S = 600`, `SESSION_TTL_S = 120`). Job Object de Cowork matando al hijo (el daemon ya lidia con breakaway, `daemon.py:1616-1618`). Un `initialize` replay incompleto deja al hijo sordo. Embedded (`app.run(stdio)` + loopback en el mismo proceso, `:5406-5437`) **no** hereda esto; el producto registrado es `--client` (`install-mcp.ps1:474-479`).

**Cómo se prueba.** Primero un harness **aislado** (otra app FastMCP de una tool, cliente `ClientSession` en el mismo script, sin tocar las sesiones vivas ni `:8765`): call → editar fuente del hijo → reciclar → call ve el código nuevo y el PID del padre no cambia. Gate de producto: en `--client` de prueba, `tool_registry_source_stale` pasa a fresco tras el verbo **sin** `reopen_mcp_client`. Luego: reciclo con lease activo y misma identidad; reciclo rechazado con heartbeat de caja; reciclo con dos `tools/call` concurrentes.

## 4. Qué NO resuelve

- **Código del daemon en el proceso que ya está en `:8765`.** Reciclar el trabajador `--client` reimporta `daemon.py` en el cliente; no sustituye el broker. Un bug en lifecycle/caja/lease sigue exigiendo política de reciclo del daemon con caja vacía — eso es otro verbo, otro día.
- **La primera activación de una tool que el proceso viejo aún no tiene** si el supervisor **aún no está desplegado**. El supervisor hay que cargarlo una vez por anfitrión; después, la IA sí se autoaplica.
- **Hot-reload de closures FastMCP** ni recarga mágica de `sys.modules` en el trabajador vivo. El trabajador nace de nuevo; eso es el punto.
- **Partidas y la caja FIFO.** No las toca si el daemon no se recicla. Tampoco transfiere estado in-memory del hijo (tasks asyncio, `_runner` cacheado) salvo lo que se reinyecte.
- **Anfitriones que cachean el catálogo y ignoran `list_changed`.** **[SUPUESTO]** si el reciclo añade tools; el caso “arreglé un bug de una tool ya listada” no depende de eso.
- **Embedded / gates que lanzan sin `--client`.** Fuera de alcance de este verbo.

## 5. Primer paso barato

No reciclar ninguna sesión viva. En menos de una hora, dos sondas:

1. **Confirmar el par padre/hijo (solo lectura).** `Get-CimInstance Win32_Process` filtrando `dayz_mcp --client`: el padre debe ser `tools\.venv-mcp\Scripts\python.exe` y el hijo `C:\Python314\python.exe` (o el `home` de `pyvenv.cfg`), mismo argv. Si es así, esa pareja **no** es el supervisor. Si no lo es, hay que reabrir el encuadre.

2. **Harness throwaway, cero producción.** Un script fuera de `tools/dayz_mcp` (scratch): padre que reenvía líneas JSON-RPC por un pipe; hijo FastMCP de una tool que lee un fichero; cliente `mcp` in-process contra el **padre**. Secuencia: `initialize` + `tools/call` → “old”; editar el fichero; el padre mata/recrea el hijo y le reenvía `initialize`; `tools/call` → “new”; el PID del padre no cambia y el cliente no reabre stdio. Si el hijo nuevo no ve `initialize`, esa es la trampa de diseño y hay que resolverla antes de tocar el servidor real. Si el reciclo funciona, la vía es viable; si el SDK o Windows se tragan el pipe, se aborta barato.

No hace falta (ni se debe) `os._exit` del MCP de nadie, ni reiniciar el daemon, ni editar `server.py` en esta sonda.

## 6. Confianza

**Media-alta** en el diagnóstico (redirector vs supervisor, refutaciones de T2, que `list_changed` no aplica código, que el daemon es el lugar equivocado para *este* bug). **Media** en que el reciclo de trabajador sea el diseño final sin un cambio de identidad de lease: la igualdad entera de `ClientIdentity` es un hueso real no ensayado.

Para subirla haría falta: el censo vivo de una pareja `--client` (sonda 1, no la corrí aquí); el harness de la sonda 2 en verde; y leer en Claude/Cursor/Codex, con un MCP dummy, qué pasa si el stdio muere — no para adoptar el suicidio, sino para no depender de un mito de reapertura.