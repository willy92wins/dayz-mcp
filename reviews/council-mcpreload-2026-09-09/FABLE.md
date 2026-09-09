# Dictamen FABLE — council mcpreload 2026-09-09

Consejero: Claude (familia Anthropic, modelo Fable). Independiente; no he leído a los otros dos.
Todo `path:line` de abajo sale de ficheros que abrí en esta sesión (árbol
`DayZ_MCP_dev/tools`, SDK `tools/.venv-mcp/Lib/site-packages/mcp` = `mcp-1.27.2`). Lo que no
pude comprobar va marcado `[SUPUESTO]`; lo que viene de documentación que consultó un agente
auxiliar y yo no abrí va como `[SUPUESTO-doc]` con su URL. Código propuesto va como `[DESIGN]`.

## 1. Recomendación (una frase) — es (A)

**(A): convertir el proceso `--client` en una pareja supervisor/trabajador dentro del mismo
comando registrado —un supervisor delgado que posee el stdio del anfitrión y reenvía JSON-RPC
línea a línea, y un trabajador (el servidor de hoy, sin cambios de registro) que se recicla
con una tool `server_reload` contestada por el supervisor tras cerrar admisión, drenar lo en
vuelo y comprobar que no hay lease/ticket/operación viva.** El agente aplica su propio arreglo
con una llamada y recibe recibo (`server_pid` nuevo y `server_modules.status=fresh` en el
siguiente `bridge_status`); ningún humano abre nada.

**Hecho previo que lo condiciona (lo que pide el brief antes de proponer).** La pareja medida
(34744 → 21388, y las otras siete parejas vivas, incluido el daemon 42108 → 23240) NO es un
supervisor del proyecto: es el redirector de venv de CPython.

- `tools\.venv-mcp\Scripts\python.exe` y `C:\Python314\Lib\venv\scripts\nt\venvlauncher.exe`
  son byte-idénticos: SHA-256 `97c3228a…9e84`, 255 320 bytes ambos; `C:\Python314\python.exe`
  es otro binario (`cce21c0e…c45b`, 106 328 bytes). `tools\.venv-mcp\pyvenv.cfg` apunta
  `home = C:\Python314`.
- En la tabla de procesos (CIM, solo lectura, hoy): cada padre tiene `ExecutablePath =
  …\.venv-mcp\Scripts\python.exe` y cada hijo `C:\Python314\python.exe`, con la MISMA
  `CommandLine`. El anfitrión lanza lo registrado en `~/.claude.json:1998-2010`
  (`command` = ese `python.exe` del venv; `install-mcp.ps1:479-496` lo genera).
- El propio árbol lo sabe: `daemon.py:1609-1613` describe «the venv redirector -- which creates
  the real interpreter with creationflags 0».
- Qué hace cada uno: el padre solo espera al hijo y propaga su código de salida; no lee ni
  escribe el protocolo (los handles de stdio se heredan al hijo). El hijo hace TODO:
  `server.py:5400` `run()` → `:5405` `build_app` → `:5437` `app.run(transport="stdio")`. No hay
  ningún `Popen` con el mismo argv en `server.py`/`__main__.py`; el único spawn del cliente es
  el del daemon (`server.py:1378-1381` → `daemon.spawn_detached`, `daemon.py:1602-1662`).

Conclusión del hecho: **no hay media solución construida** (el padre no sostiene ni sesión ni
estado ni puede relanzar), pero la forma sí demuestra dos cosas que hacen barata la mía: (1) el
anfitrión tolera que el proceso que lanzó sea una pasarela y otro haga el trabajo; (2) los
chequeos de procedencia miran `sys.executable` resuelto y las dos registraciones, no la cadena
de padres (`host_config.py:131-138`, `:242-246`, `:330-360`); la acreditación del padre solo se
ejecuta para la política `kind == "bootstrap"` del lanzador nativo (`daemon_policy.py:381-388`),
que el cliente MCP no usa (`server.py:1122` carga la `normal`).

## 2. Por qué esa y no las otras

- **Hot reload del registro FastMCP (B) — no.** Coincido con la refutación: los closures de
  esquema (`server.py:1935-1943`, `:1955-1963`), el `isinstance(runtime, ClientRuntime)`
  (`:3331`), `_SERVER_SOURCES` congelado en el import (`:65`, tras cargar cierres de producción
  en `:61-63`) y el ControlClient (`control_client.py:161-166`) no son hojas. Reciclar el
  proceso entero los esquiva todos: no se recarga nada, se sustituye.
- **Suicidio + reapertura por el anfitrión — no, y por una razón más.** Además de las tres de
  RONDA3 §2, Claude Code no relanza servidores stdio caídos: la recuperación es `/mcp`
  interactivo o sesión nueva `[SUPUESTO-doc: code.claude.com/docs/en/mcp «Stdio servers: No
  automatic reconnection»; issues anthropics/claude-code #43177, #54136]`. El supervisor
  ataca las tres razones concretas de RONDA3: (a) `_release_and_exit` (`server.py:5389-5397`)
  no se usa: el trabajador sale por EOF de stdin, que el SDK convierte en cancelación limpia
  (`lowlevel/server.py:685-690`), y si no sale, Job Object; (b) «volver de la tool no prueba
  el flush»: la respuesta que ve el anfitrión la escribe el supervisor, que no se recicla, y
  el supervisor decide reciclar solo cuando ya tiene en mano los bytes de respuesta del
  trabajador; la propia `server_reload` la contesta el supervisor, así que no existe el
  «respondo y muero»; (c) «una foto de idle no cierra la admisión»: el supervisor ES la
  admisión: conoce el conjunto exacto de `id` reenviados y pendientes; cierra la puerta antes
  de mirar, no hace una foto.
- **Tools tras la frontera IPC del daemon (dirección 2) — no.** El daemon no ejecuta tools:
  reenvía comandos de bridge (`/enqueue` `loopback.py:3017-3019`, `/await` `:2999-3002`) y
  coordina sesión/lifecycle (`:133-148`). La lógica de tool vive en el cliente (57 `@app.tool`,
  45 sitios `call_bridge` en `server.py`; el reenvío con identidad y lease en `:1589-1600`).
  Moverla mete lo que más cambia en el proceso que no puede morir (LL-156; `server.py:5418-
  5424`) y que aloja runs ajenos (`daemon.py:413`, `:520`, `:598`): para aplicar código habría
  que reiniciar justo lo que la política prohíbe. Empeora la localidad.
- **Adaptador estable + implementaciones recargables (dirección 3, B) — no.** Es el hot reload
  con otro abrigo: todo lo capturado en `build_app` sigue capturado y `server.py` (5 441
  líneas, donde viven casi todos los bugs) no es hoja. Parcial por construcción; el coste por
  hoja ya está medido: 22 tests para un solo módulo (`playbook_reload`, RONDA3 §3).
- **Afordancias del anfitrión/protocolo (dirección 4) solas — no bastan, pero se usan.**
  `notifications/tools/list_changed` existe en el SDK (`mcp/server/session.py:477-479`,
  `types.py:1372-1378`) y Claude Code lo honra desde v2.1.0 con el runtime v2 `[SUPUESTO-doc:
  mismo doc; issues #50339, #31893]`; solo hace que el anfitrión repita `tools/list`, no cambia
  el código que corre. Es el paso 6 de mi propuesta, no una alternativa.
- **Reiniciar el daemon — fuera de alcance.** El problema planteado es código del proceso de
  tools; el daemon ya vigila su frescura (`daemon.py:650-672`) y ya rechazó recargarse
  (`daemon.py:601-604`; councils 2026-08-07, `plans/…-plan-v2.md:27`). No lo toco.

## 3. Coste y riesgo

**Qué hay que tocar `[DESIGN]`.**

1. `tools/dayz_mcp/supervisor.py`, nuevo, ~300-400 líneas, sin FastMCP: bomba de líneas
   JSON-RPC entre stdin/stdout del anfitrión y los pipes del trabajador; guarda los params del
   `initialize` del anfitrión y la última `tools/list` servida; mantiene `in_flight: set[id]`
   y una puerta de admisión con cola; intercepta solo dos cosas: `tools/list` (añade
   `server_reload` al resultado) y `tools/call name=server_reload`. Todo lo demás pasa
   opaco por dirección (las notificaciones de progreso del trabajador, `server.py:3481`,
   también).
2. `server.py:run()` (`:5400-5440`): en `--client`, si NO está `DAYZ_MCP_WORKER=1` → arranca el
   supervisor; si está → el camino de hoy. ~10 líneas. **Discriminador por entorno, no por
   argv**: el registro de `~/.claude.json` y `~/.codex/config.toml` no cambia, y el parser
   estricto de la registración (`host_config.py:247-279`) ni se entera. El patrón de marcar
   hijos por entorno ya existe (`daemon.py:978-983`).
3. Spawn del trabajador: `[sys.executable, "-m", "dayz_mcp", *argv_de_hoy]` con pipes,
   `CREATE_NO_WINDOW` y Job Object `KILL_ON_JOB_CLOSE`; el SDK ya trae exactamente eso
   (`mcp/os/win32/utilities.py:136`, `:173-174`, `:241-245`, `:271`, `:281`; `pywin32-312`
   está en el venv). Como `sys.executable` es el `python.exe` del venv, el trabajador resulta
   otra pareja redirector+intérprete y `_local_launch_executable()` (`host_config.py:131-138`)
   da lo mismo que hoy.
4. Stub `server_reload` registrado en `build_app` (para que el inventario de esquema y los
   contadores de docs sigan siendo honestos: `tests/test_install_mcp.py:1263-1328` y la
   fixture `profile_inventory.json` que RONDA3 tuvo que tocar). En bare/embedded devuelve
   `server_reload_unavailable: no_supervisor`; bajo supervisor nunca llega al trabajador.
5. Oráculo de quiescencia: el trabajador no publica hoy sus `active_lease_token/ticket/
   operation_id` (`control_client.py:161-163`; `bridge_status_payload` de cliente en
   `server.py:1769-1780` no los lleva). Una línea: publicar `session_quiescent: bool` en
   `bridge_status`, o preguntar al daemon por `session_status` (`server.py:3460-3465`) y
   filtrar por `session_id` `[SUPUESTO: nombres exactos de campos de /session/status]`.

**Secuencia de `server_reload` `[DESIGN]`** (azul/verde, nunca se mata al viejo antes de que el
nuevo esté listo): cerrar admisión (las peticiones nuevas del anfitrión se encolan) → esperar a
`in_flight == ∅` con tope (por defecto 30 s; si no, reabrir y rehusar con `in_flight=[ids]`)
→ preguntar quiescencia; si hay lease/ticket/operación → reabrir y rehusar
(`server_reload_refused: lease_held|ticket_queued|operation_active`, el mismo patrón que
`playbook_tool.py:158-170`) → lanzar trabajador NUEVO, reenviarle `initialize` con los params
guardados y `notifications/initialized` (el SDK exige ese orden: `mcp/server/session.py:165-
193`, `:199-200`), pedirle `tools/list` → si falla (import roto, excepción) → matarlo, reabrir
admisión, rehusar con la causa; el viejo sigue sirviendo → si va bien: cerrar stdin del viejo,
esperar ≤5 s, si no Job Object → conmutar enrutado → si la `tools/list` difiere, enviar
`notifications/tools/list_changed` al anfitrión → soltar la cola → responder
`{"status":"reloaded","old_pid","new_pid","tools_changed":bool}`. Dos trabajadores coexisten
unos segundos sin conflicto: en modo cliente no se bindea nada (`server.py:3291-3295`) y el
daemon se descubre perezosamente (`:1433-1441`). Un lease huérfano no puede quedar: la
precondición lo excluye y, aun así, el daemon expira por TTL (`session_coordination.py:15`
`SESSION_TTL_S = 120`, `:2084-2093`), que es el contrato de «proxy stdio desechable» ya
diseñado en `plans/2026-07-14-agent-session-coordination-implementation.md:412`, `:516`.

**Qué se puede romper.**

- Líneas grandes: `capture_screenshot` devuelve base64 de MB; el `limit` por defecto de
  `asyncio.StreamReader` (64 KiB) rompería la bomba. Fijar `limit` alto o leer sin límite.
- stdin del anfitrión en Windows: no se puede envolver en asyncio directamente; hilo lector
  → cola (el SDK hace lo equivalente con `anyio.wrap_file`, `mcp/server/stdio.py:46-48`).
- Capacidad `listChanged`: la que declara el trabajador en `initialize` es la de FastMCP
  (`mcp/server/session.py:176`), probablemente `false` `[SUPUESTO]`; el supervisor debe
  reescribir ese campo a `true` en el resultado que reenvía, porque es él quien notifica.
- Petición trabajador→anfitrión pendiente durante un swap (sampling/elicitation): hoy no se
  usa ninguna (`server.py` solo usa `ctx.report_progress`, `:3407`, `:3481`); si apareciera,
  la respuesta llegaría a un trabajador muerto: descartar con log.
- Cierre de sesión: EOF del anfitrión → el supervisor cierra stdin del trabajador y espera;
  el Job Object cubre el caso en que maten al supervisor.
- `ClientIdentity.ppid` pasa a ser otro redirector (`server.py:1210-1217`); es informativo:
  `session_coordination.py:60-92` lo parsea y no encontré uso de vivacidad por ppid.
- Drenaje largo: `wait_for` admite ≤600 s y `ui_dialog` ≤250 s (`server.py:5326-5334`);
  `server_reload` rehúsa antes que cancelar: nunca se corta un `dayz_test_run` a mitad de
  lanzamiento (lo que cuesta un lanzamiento no confirmado: `process_lifecycle.py:3351-3365`,
  `:3423`).

**Cómo se prueba.**

- Unitario, sin procesos: bomba con streams falsos — seguimiento de `id`, puerta de admisión,
  tope de drenaje, replay de `initialize`, `list_changed` solo si la lista cambia, cada
  motivo de rechazo. Mismo estilo que `tests/test_client_runtime_control_composition.py`.
- Integración offline, sin juego ni daemon: trabajador REAL por pipes con una app que importa
  un módulo temporal (el patrón de `verify_s3.py` y `test_playbook_reload.py:105`): `old` →
  editar → `server_reload` → `bridge_status.server_modules.server_pid` cambia, `stale=[]`,
  la tool devuelve `new`. Añadir: nuevo trabajador roto (SyntaxError) → rechazo con causa y el
  viejo sigue contestando; reload con lease simulado → rechazo.
- Gate vivo, una sesión nueva, sin tocar el juego: `bridge_status` (pid A) → edición inocua →
  `server_reload` → `bridge_status` (pid B, `fresh`) → `session_status` del daemon igual que
  antes (las otras sesiones ni se enteran). Si el anfitrión honra `list_changed`, una tool
  nueva aparece sin reabrir; si no, ese hueco queda documentado (§4).

## 4. Qué NO resuelve

1. **Código del daemon.** `daemon.py`, `loopback.py`, `session_coordination.py`,
   `process_lifecycle.py` siguen exigiendo reinicio del daemon, gobernado por la política de
   runs ajenos. El cliente ya separa `daemon_modules` de `server_modules`
   (`server.py:3267-3269`); el supervisor no promete nada sobre el primero.
2. **Recargar con lease/ticket/operación viva o a mitad de un `wait_for` largo.** Se rehúsa,
   no se fuerza. El agente suelta el lease o espera.
3. **Modos bare/embedded.** El puerto pertenece al proceso (`server.py:915-929`,
   `:5418-5435`); ahí no hay supervisor. Los gates in-game 0-4 siguen idénticos y siguen sin
   recarga. Alcance: solo `--client`.
4. **El supervisor no se recarga a sí mismo.** Es la nueva «no hoja»; por eso debe ser tonto
   y pequeño (~1/15 de `server.py`). Un bug ahí sigue costando reabrir la sesión.
5. **Vista del anfitrión tras el swap.** Si el anfitrión no honra `list_changed` (runtime
   antiguo, Codex, Cursor), las tools existentes funcionan con el código nuevo, pero los
   nombres nuevos y los esquemas cambiados esperan a una reapertura; un desajuste de esquema
   aflora como `bad_args` del trabajador. `serverInfo`/`instructions` del `initialize` no se
   renegocian.
6. **No es flota.** Cada sesión recarga su trabajador; las otras siguen rancias hasta que
   llamen a `server_reload` (su `bridge_status` ya lo dice).
7. **No es sandbox.** Un código nuevo que importa bien pero se comporta mal se aplica igual;
   solo se protege el fallo de arranque (azul/verde).

## 5. El primer paso barato (< 1 h, sin tirar sesiones)

Un script en el scratchpad, ~80 líneas, `spike_supervisor.py` `[DESIGN]`, que NO toca el
árbol, NO mata nada y NO lanza DayZ:

1. `Popen` de exactamente el comando registrado (`~/.claude.json:2000-2010`) con `stdin/
   stdout=PIPE`, `stderr` heredado, `CREATE_NO_WINDOW`; es una pareja nueva, ajena a las
   ocho vivas.
2. Escribir `initialize` (`protocolVersion` "2025-06-18", `clientInfo` ficticio),
   `notifications/initialized`, `tools/list`, y `tools/call bridge_status` (lectura: el daemon
   23240 está vivo, no se dispara spawn). Imprimir `capabilities.tools.listChanged`, el número
   de tools (esperado 57 + knowledge) y `server_modules.server_pid`.
3. Cerrar stdin y cronometrar hasta `returncode` (¿sale limpio por EOF y en cuánto?).
4. Repetir 1-3 con un segundo trabajador mientras el primero aún vive, para ver que dos
   coexisten sin `daemon_provenance_conflict` ni bind.

Decide: si (a) inicializa por pipes, (b) `bridge_status` responde sin conflicto de
procedencia, (c) sale por EOF en < 5 s y (d) dos coexisten, la vía es viable y el siguiente
paso es el supervisor con el gate offline de §3. Si (a) o (b) fallan, la forma de spawn (env
var, `sys.executable`) necesita rehacerse antes de escribir una línea del supervisor. Si (c)
falla, el cierre pasa a Job Object por defecto y se anota. Lo que este spike no prueba: que
Claude Code repita `tools/list` al recibir `list_changed`; eso solo se ve con una sesión nueva
y código en el árbol, y va al gate vivo.

## 6. Confianza y qué me faltó

**Media-alta** en el diagnóstico de la pareja (hash idéntico, tabla de procesos, y el árbol lo
documenta) y en la mecánica del SDK (abierto: admisión concurrente `lowlevel/server.py:673-
683`, cancelación por cierre de transporte `:685-690`, `respond` `:800`, flush en otro task
`stdio.py:75-81`, gating de `initialize` `session.py:165-200`). **Media** en el diseño del
supervisor: no está prototipado.

Me faltó: (1) ejecutar el spike de §5 (stdin en Windows, líneas grandes, salida por EOF);
(2) abrir yo la documentación de Claude Code sobre reconexión stdio y `list_changed` y qué
gatea `MCP_PROTOCOL_NEGOTIATION` (lo leyó un agente auxiliar; de ahí los `[SUPUESTO-doc]`);
(3) el payload exacto de `/session/status` para el oráculo de quiescencia; (4) qué declara
FastMCP en `capabilities.tools.listChanged`; (5) una lectura de `test_daemon_spawn_branch.py`
para reutilizar sus fixtures de procesos en el gate offline. Con (1) y (2) subiría a alta.
