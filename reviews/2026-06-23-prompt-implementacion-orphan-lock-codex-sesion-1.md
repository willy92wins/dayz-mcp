# Prompt implementación — orphan-lock del server MCP (Codex sesión 1)

> Patrón: implementation handoff (codex-handoff-template). Destinatario: Codex (impl) + Claude (receptor).
> Origen: 2026-06-23 — el smoke de MercedesAMGLF Fase 2 se bloqueó porque `dayz-mcp` dio
> `× Failed to connect`: un server huérfano (PID 84468, mismo command line, arrancado 13:22) seguía
> escuchando en :8765 y el lock E4 rechazó el bind de la sesión nueva. Es la 2ª vez que el huérfano
> muerde entre sesiones. Diagnóstico de causa raíz hecho por Claude (cite-then-verify) — va dentro del prompt.

Copia de marcador a marcador:

```
===== PROMPT INICIO =====

Tarea: blindar el server MCP `dayz-mcp` contra procesos huérfanos que retienen el puerto 8765
entre sesiones. Esta sesión cubre **únicamente** dos mecanismos acoplados (una sola feature
"orphan-proof"): (1) un watchdog de muerte-del-parent que termina el server cuando su proceso
padre muere, y (2) un auto-reclaim al arranque que recupera el puerto si lo retiene un huérfano
`dayz_mcp` cuyo parent ya murió. NO toques las 11 tools, el bridge Enforce, el flujo de install,
ni la semántica del lock E4 frente a instancias VIVAS. No implementes nada más ni siquiera parcialmente.

## Diagnóstico de causa raíz (ya verificado por Claude, cite-then-verify — NO lo re-derives)

- El cierre LIMPIO ya funciona: `app.run(transport="stdio")` (dayz_mcp/server.py:552) retorna al
  EOF de stdin → el `lifespan` (server.py:293-303) ejecuta `runtime.stop_loopback()` →
  `LoopbackServer.stop()` (dayz_mcp/loopback.py:459-468) hace `httpd.shutdown()` + `httpd.server_close()`
  → puerto liberado. El hilo HTTP es daemon (loopback.py:450-454), así que NO sostiene el proceso por sí solo.
- El huérfano aparece cuando el parent (Claude Code / Cowork) muere ABRUPTAMENTE en Windows: el stdin
  del hijo no recibe EOF → `app.run()` bloquea para siempre en el hilo principal → el proceso sigue vivo
  → el puerto 8765 queda retenido. En la siguiente sesión, el bind exclusivo de E4
  (`ExclusiveThreadingHTTPServer`, `allow_reuse_address=False`, loopback.py:402-417) falla con
  EADDRINUSE → la sesión nueva ve `× Failed to connect` y las tools `dayz-mcp` no cargan.
- Conclusión: el server necesita (1) morir cuando muere su parent (elimina la causa) y (2) recuperar
  el puerto de huérfanos legacy / casos donde el watchdog no llegó a correr (p.ej. el proceso fue
  matado con SIGKILL antes de limpiar).

## Carga inicial obligatoria

Lee estos archivos antes de tocar nada (paths absolutos Windows):

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py
   (entry `run()` :549-553, `lifespan` :293-303, `Runtime.start_loopback/stop_loopback` :54-73 — donde
   se instala el watchdog y donde se invoca el bind).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py
   (`ExclusiveThreadingHTTPServer` + `create_http_server` :402-417, `LoopbackServer.start/stop` :446-468
   — donde engancha el auto-reclaim, justo ANTES del bind exclusivo).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_instance_lock.py
   (test del lock E4; extiende este estilo para los tests nuevos, stdlib unittest).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md
   (bloque LIVE-STATE: invariantes cerradas; en especial "Lock E4 `ExclusiveThreadingHTTPServer`
   (NO tocar `allow_reuse_address`)" — el reclaim debe respetarla).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (stack stdlib-only del server, fail-closed R6, convenciones del proyecto).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\bug-ledger.md
   (asigna un BUG-NNN nuevo a este hardening; mira la numeración del backlog BUG-024..031).

NO leas el árbol `.venv-mcp\` salvo para confirmar una firma stdlib concreta. NO abras los handoffs
de sesiones anteriores (`AI/30_Sessions/...`) salvo que necesites un contrato no citado aquí — si lo
haces, justifícalo en el handoff final.

## Alcance acotado — feature "orphan-proof" (una sola entrega, 2 mecanismos)

### Parte 1 — Watchdog de muerte-del-parent (mecanismo primario, must-have)

Objetivo: cuando el proceso padre del server muere, el server libera el puerto y termina, sin
depender de que el stdin reciba EOF.

Especificación (verificable):
- Capturar el parent en el arranque (en `run()` antes de `app.run()`, o al inicio del `lifespan`):
  `os.getppid()` + un handle al parent vía ctypes `OpenProcess(SYNCHRONIZE, False, ppid)` (kernel32).
- Lanzar un hilo **daemon** que bloquee en `WaitForSingleObject(parent_handle, INFINITE)`. Cuando
  retorne `WAIT_OBJECT_0` (el parent murió): hacer un `loopback.stop()` best-effort (libera el puerto
  vía `server_close()`) y luego `os._exit(0)`. Hard-exit es correcto: el parent ya no está, no hace
  falta cierre MCP grácil.
- Loggear a stderr en el arranque el PPID capturado + nombre de imagen del parent (`LOG: parent pid=… name=…`),
  para diagnosticar si el parent fuese un shell transitorio en vez del Claude Code/node real.
- **Fail-safe de arranque**: si `OpenProcess` falla (parent ya no existe en el arranque) o el PPID
  parece un proceso ya muerto, NO hagas `os._exit` inmediato — loggea `WATCHDOG: parent handle unavailable,
  watchdog disabled` y deja correr el server normal (fallback al comportamiento actual por EOF de stdin).
  El watchdog jamás debe tumbar un arranque sano.
- Multiplataforma: en no-Windows, fallback a un poll de `os.getppid()` (si cambia a 1 = reparentado → exit).
  El entorno real es Windows; el path ctypes/WaitForSingleObject es el que importa.

Test obligatorio (Parte 1):
- Integración: arranca el server (o un stub mínimo que instale solo el watchdog + un loopback en un
  puerto efímero) como hijo de un parent de vida corta; mata el parent; asserta que el proceso hijo
  termina en ≤ N s (p.ej. 5 s) y que el puerto queda libre/rebindeble. Si el integration test es
  inviable de forma determinista en CI, deja al menos un unit test de la lógica de decisión del
  watchdog (handle disponible → arma; handle ausente → deshabilita sin exit) y documenta el gap.

### Parte 2 — Auto-reclaim del puerto al arranque (mecanismo secundario, belt-and-suspenders)

Objetivo: si al bindear el puerto ya lo retiene un huérfano `dayz_mcp` cuyo parent murió, recuperarlo;
si lo retiene una instancia VIVA (parent vivo), NO tocarla (preserva E4 fail-closed).

Patrón recomendado (mínimo y quirúrgico, mantiene el bind exclusivo de E4 intacto):
- En el camino de bind (`create_http_server` o `Runtime.start_loopback` justo antes de `loopback.start()`):
  intenta el bind exclusivo como ahora. Si lanza `OSError`/EADDRINUSE, ejecuta el discriminador de
  reclaim; si procede, mata al huérfano, espera a que el puerto se libere, y **reintenta el bind UNA vez**.
  Si el reintento falla o el discriminador dice "no es huérfano recuperable", re-lanza el error (fail-closed).
- NO cambies `allow_reuse_address` (sigue `False`). El reclaim NO relaja el lock: solo retira un
  huérfano confirmado-muerto antes de volver a intentar el bind exclusivo.

Discriminador de reclaim — mata al proceso que escucha en `port` SOLO si se cumplen TODAS:
1. El PID que escucha es un intérprete Python.
2. Su command line contiene `dayz_mcp` Y `--port <este_port>` (es uno de los nuestros, mismo puerto).
3. Es huérfano real: su proceso padre ya NO está vivo (con el watchdog desplegado, una instancia viva
   siempre tiene parent vivo; "parent muerto" ⟺ huérfano que no llegó a auto-limpiarse).
Si alguna falla → NO matar, re-lanzar EADDRINUSE (puede ser una instancia legítima viva de otra sesión).

Notas de implementación (stdlib, sin psutil):
- port → PID: parsea `netstat -ano` (subprocess) buscando `:<port>` en estado LISTENING, o usa
  `iphlpapi.GetExtendedTcpTable` vía ctypes. Elige una y deja claro por qué.
- PID → command line: ctypes `CreateToolhelp32Snapshot`/`Process32First/Next` da imagen; para el
  command line completo usa `wmic process where processid=<pid> get commandline` (subprocess) o la
  API Win32 que prefieras. PID → PPID: `th32ParentProcessID` del snapshot ToolHelp32.
- liveness del parent: `OpenProcess(SYNCHRONIZE, …)` + `WaitForSingleObject(h,0)` (WAIT_OBJECT_0 = muerto)
  o `GetExitCodeProcess` (STILL_ACTIVE = vivo).

Test obligatorio (Parte 2):
- Unit del discriminador (es el test de seguridad clave — que NO mate lo que no debe): dada una tripleta
  (es_python, cmdline, parent_vivo), asserta la decisión kill/no-kill. Cubre: huérfano nuestro con
  parent muerto → kill; instancia nuestra con parent vivo → NO kill; proceso ajeno (no `dayz_mcp`) → NO kill;
  mismo `dayz_mcp` pero distinto `--port` → NO kill.
- Integración (si viable): bindea un "squatter" `ExclusiveThreadingHTTPServer` cuyo command-line simule
  un `dayz_mcp --port <p>` y orfánalo; invoca el reclaim; asserta que libera el puerto y que un bind
  exclusivo posterior tiene éxito. Si es frágil en CI, documenta el gap y deja el unit del discriminador.

### Suite de tests

- Añade los tests nuevos a `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\`
  (estilo `test_instance_lock.py`, stdlib `unittest`, sin pytest). Nombres sugeridos:
  `test_parent_watchdog.py`, `test_port_reclaim.py`.
- La suite existente DEBE seguir verde. Baseline declarado en el HANDOFF: **38/38 (venv) + 20/20 (stdlib)**.
  Córrela como la corre el proyecto (unittest discover sobre `tests\` con el python de `.venv-mcp\`):
  desde `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\`
  → `.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v`. Si el runner real difiere,
  reconcilia y repórtalo en el handoff. Pega el output literal en el Bloque B.

## Restricciones críticas (vinculantes toda la sesión)

1. **Solo stdlib** (el server es stdlib-only por diseño — ver CLAUDE.md / HANDOFF "mcp NO en el user site").
   Permitido: `os`, `sys`, `threading`, `ctypes`, `subprocess`, `socket`, `time`, `signal`. NINGUNA dep
   nueva (NO psutil). Cualquier `import` de tercero es regresión.
2. **E4 intacto**: `ExclusiveThreadingHTTPServer.allow_reuse_address` se queda en `False`. El reclaim
   NO relaja el lock; solo retira huérfanos confirmados-muertos y reintenta el bind exclusivo. Una
   instancia VIVA (parent vivo) NUNCA se mata (fail-closed R6 preservado).
3. **No toques fuera de scope**: las 11 tools y sus validaciones, el bridge Enforce (`MCPMessages.c`/
   `MCPBridge.c`/`MCPClientBridge.c`), `install-mcp.ps1`, el flujo de versión/exec chokepoint
   (`loopback.py` record_poll / _enqueue_exec_enforce). No los "mejoras de paso".
4. **NO improvises fuera de este spec**. Si algo no encaja (una API ctypes que no se comporta, un test
   de integración no determinista en Windows), NO improvises un atajo: anótalo en el Bloque C con
   path:line, aplica la interpretación conservadora (p.ej. degradar a unit test + documentar el gap),
   y marca para revisión.
5. **R2 cite-then-verify**: cada firma stdlib/ctypes que uses, verifícala antes de escribirla. No cites
   de memoria APIs Win32.
6. **R21 doble revisión NO se hace aquí**. Implementa, testea, y para. No te autorrevises ni hagas
   pasadas de cleanup adicionales. La revisión a fondo es sesión separada (la hará Claude como receptor).
7. **El gate in-vivo NO es tuyo**: confirmar contra el parent REAL de Cowork (re-registrar, abrir sesión
   nueva, ver `√ Connected` + tools cargadas + que el watchdog NO tumbó un server sano + que un huérfano
   plantado se auto-recupera) requiere el Claude Code real y lo hace el receptor. Tú entregas mecanismo
   + tests offline. Declara explícitamente este límite en el Bloque D.

## Output esperado al cerrar la sesión

### Bloque A — Archivos creados/modificados
Lista con paths absolutos y tamaño/líneas aprox. (server.py y/o loopback.py modificados; tests nuevos;
bug-ledger.md con el BUG-NNN asignado).

### Bloque B — Resultado de los tests
Output literal (no parafraseado) de la suite completa: discover venv (baseline 38/38 + tests nuevos)
y el run stdlib si aplica. Si algún integration test se degradó a unit por no-determinismo, indícalo aquí.

### Bloque C — Hallazgos durante la implementación
Por cada problema: archivo/sección, qué no encajaba, acción aplicada (o "marcado para resolución"),
sugerencia. Si ninguno: "Sin hallazgos." Declara aquí cualquier API que no se comportó como esperabas.

### Bloque D — Handoff técnico para el receptor (Claude)
- Estado al cierre (mecanismos entregados, cobertura de tests, gaps).
- Insertion points exactos que tocaste (path:line) para que el receptor verifique scope por mtime.
- El gate in-vivo pendiente (Cowork real): pasos concretos para que el receptor lo ejecute.
- Cómo plantar un huérfano de prueba para validar el reclaim manualmente.

===== PROMPT FIN =====
```

## Para el receptor (Claude, sesión siguiente)

- Verificar Bloque A path-a-path (Read), Bloque B que parezca output real de unittest, Bloque C → inbox.
- **Gate in-vivo (solo el receptor puede)**: re-registrar si hace falta, abrir sesión Cowork nueva,
  confirmar `claude mcp list = √ Connected` + tools `dayz-mcp` cargadas (el watchdog NO tumbó el arranque),
  y validar el reclaim plantando un huérfano en :8765 antes de arrancar.
- Actualizar la invariante del HANDOFF ("Lock E4 …") para reflejar que el server ahora es self-healing
  (watchdog + reclaim), y la fila de TROUBLESHOOTING de la skill `dayz-mcp-verify` (el "mata el squatter
  a mano" pasa a "el server lo auto-recupera; si no, el watchdog/reclaim regresó").
