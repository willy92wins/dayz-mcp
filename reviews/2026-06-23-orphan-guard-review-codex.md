# R21 adversarial - orphan_guard

## Resumen ejecutivo

VEREDICTO: UNSOUND.

E4 no esta relajado: `ExclusiveThreadingHTTPServer.allow_reuse_address = False` sigue en `tools/dayz_mcp/loopback.py:405-410`, el retry vuelve a instanciar esa clase en `tools/dayz_mcp/loopback.py:418-430`, y `HANDOFF.md:18` conserva la invariante "NO tocar allow_reuse_address". El problema esta alrededor del lock: el reclaim puede matar procesos incorrectos, el watchdog no cubre la topologia productiva C1, y el gate local no esta verde en esta ejecucion.

Verificacion ejecutada desde `tools`: `.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v` -> `Ran 59 tests ... FAILED (failures=1)`. El fallo fue `tests.test_port_reclaim.ReclaimIntegrationTest.test_dead_parent_orphan_is_reclaimed_end_to_end`: `try_reclaim_port()` devolvio `False` porque el cmdline real no fue recuperable. Probe adicional: `orphan_guard.command_line_of(os.getpid())` devuelve `None` en este host; `wmic process where ... get commandline /value` falla con `process - Alias not found` y `Get-CimInstance Win32_Process` devuelve acceso denegado.

## Matriz de hallazgos

| ID | file:line | Severidad | Resumen | Fix de 1 linea |
|---|---|---:|---|---|
| OG-P1-01 | `tools/dayz_mcp/orphan_guard.py:389` | FAIL/P1 | El marcador `dayz_mcp` es substring global del cmdline; con el venv bajo `DayZ_MCP_dev`, un Python ajeno con `--port 8765` puede ser reclamado. | Exigir identidad exacta del server (`-m dayz_mcp` / entrypoint propio), no substring en path/cmdline. |
| OG-P1-02 | `tools/dayz_mcp/orphan_guard.py:435` | FAIL/P1 | Parent PID desconocido se interpreta como parent muerto y permite matar una instancia viva si ToolHelp falla. | Si `get_parent(pid)` devuelve `None`, devolver `False` y preservar E4. |
| OG-P1-03 | `tools/dayz_mcp/server.py:564` | FAIL/P1 | El watchdog vigila solo el parent inmediato; en la topologia `Claude Code -> launcher A -> server B`, la muerte de Claude no dispara el watchdog ni habilita reclaim. | No declarar orphan-proof hasta vigilar el ancestro correcto o registrar un Python sin launcher. |
| OG-P1-04 | `tools/tests/test_port_reclaim.py:203` | FAIL/P1 | El gate de reclaim no prueba la via real de liveness y, en esta ejecucion, el integration real falla por cmdline inaccesible. | Hacer que el integration use parent-liveness real y que cmdline retrieval pase en el host objetivo antes de aceptar el feature. |
| OG-P2-01 | `tools/dayz_mcp/orphan_guard.py:121` | WARN/P2 | `WaitForSingleObject` ignora `WAIT_FAILED`; cualquier retorno ejecuta el callback que acaba en `os._exit(0)`. | Solo llamar `on_parent_death` si el retorno es `WAIT_OBJECT_0`; en `WAIT_FAILED`, cerrar, loggear y no salir. |
| OG-P2-02 | `tools/dayz_mcp/orphan_guard.py:203` | WARN/P2 | `listener_pid_for_port` devuelve la primera fila LISTENING que acaba en `:<port>`, sin acotar al endpoint E4 `127.0.0.1`. | Filtrar la local address que bloquea `127.0.0.1:<port>` y revalidar antes de matar. |

## Hallazgos detallados

### OG-P1-01 - Reclaim sobre-mata por marcador `dayz_mcp` demasiado amplio

`should_reclaim_listener()` acepta cualquier proceso Python cuyo command line contenga la substring `dayz_mcp` (`tools/dayz_mcp/orphan_guard.py:389`) y el `--port` exacto (`tools/dayz_mcp/orphan_guard.py:391`). Eso no prueba que el listener sea el server MCP. El venv esta dentro de `DayZ_MCP_dev`: `tools/.venv-mcp/pyvenv.cfg:5` contiene esa ruta, asi que un Python ajeno lanzado desde ese entorno puede aportar `dayz_mcp` por path aunque ejecute otra cosa.

Modo de fallo concreto: `C:\...\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe -m http.server --port 8765` queda con parent muerto y escucha el puerto. La funcion pura confirma el problema: con `listener_is_python=True`, ese cmdline y `parent_alive=False`, `should_reclaim_listener(..., port=8765)` devuelve `True`. Luego `try_reclaim_port()` llega a `kill(pid)` en `tools/dayz_mcp/orphan_guard.py:450-453`.

La suite no cubre el negativo real. El caso "foreign process" usa `C:\python.exe -m http.server --port 8765` en `tools/tests/test_port_reclaim.py:65`, sin el path real `DayZ_MCP_dev`. En cambio, el integration lanza un squatter que no es `-m dayz_mcp` y le agrega un argumento suelto `dayz_mcp` (`tools/tests/test_port_reclaim.py:184-185`), y espera que sea matado (`tools/tests/test_port_reclaim.py:205`). Eso entrena el falso positivo en vez de bloquearlo.

### OG-P1-02 - Parent desconocido se interpreta como parent muerto

`try_reclaim_port()` hace `ppid = get_parent(pid)` y despues `parent_alive = is_alive(ppid) if ppid else False` (`tools/dayz_mcp/orphan_guard.py:434-435`). Ese `else False` colapsa "parent confirmado muerto" con "no pude obtener parent".

`parent_pid_of()` depende de `_toolhelp_lookup()` (`tools/dayz_mcp/orphan_guard.py:169-171`). `_toolhelp_lookup()` devuelve `None` si `CreateToolhelp32Snapshot` falla (`tools/dayz_mcp/orphan_guard.py:153-155`) o si la snapshot no encuentra el proceso (`tools/dayz_mcp/orphan_guard.py:159-164`). En ambos casos, la rama actual trata el parent como muerto. Probe inyectado de solo lectura: `try_reclaim_port(..., get_parent=lambda pid: None, is_alive=raise_if_called, kill=record, wait_free=True)` devuelve `True` y registra `[4321]`.

Modo de fallo concreto: una instancia viva `python -m dayz_mcp --port 8765` mantiene parent vivo, pero ToolHelp falla al consultar el parent. Si `image_name_of()` y `command_line_of()` si devuelven datos, los otros filtros pasan y el codigo mata una instancia que no estaba confirmada como orphan. Esto contradice el propio contrato "All four must hold (any miss => keep the port, re-raise EADDRINUSE, fail-closed)" en `tools/dayz_mcp/orphan_guard.py:383-385`.

### OG-P1-03 - Watchdog no protege la topologia productiva C1

`install_parent_death_watchdog()` captura solo `os.getppid()` (`tools/dayz_mcp/orphan_guard.py:330`), abre handle a ese PID (`tools/dayz_mcp/orphan_guard.py:337`) y arranca un thread contra ese handle (`tools/dayz_mcp/orphan_guard.py:343-350`). `server.run()` instala ese watchdog sin logica adicional de ancestros (`tools/dayz_mcp/server.py:561-568`). El callback termina en `runtime.stop_loopback()` + `os._exit(0)` (`tools/dayz_mcp/server.py:551-558`).

C1 esta confirmado en el ledger: `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md:39` documenta la topologia `Claude Code -> launcher A -> server B`, con el watchdog vigilando A. Si muere Claude Code, A sobrevive esperando a B; el watchdog no dispara y el reclaim de la siguiente sesion ve el parent A vivo. `tools/.venv-mcp/pyvenv.cfg:4-5` confirma que este venv redirige a `C:\Python314\python.exe`.

El integration no prueba esa muerte de abuelo. El test reconoce que `Popen` devuelve el parent inmediato vigilado (`tools/tests/test_parent_watchdog.py:94-96`), comprueba `watched_parent == proc.pid` y si no, hace skip (`tools/tests/test_parent_watchdog.py:104-108`), y mata precisamente ese parent inmediato (`tools/tests/test_parent_watchdog.py:115-118`). Eso prueba "si muere A, B sale"; no prueba "si muere Claude Code, B sale", que es el caso productivo.

### OG-P1-04 - Gate de reclaim incompleto y actualmente rojo en este host

El integration de reclaim fuerza `is_alive=lambda pid: False` (`tools/tests/test_port_reclaim.py:203`). Con eso no ejerce la ruta real `parent_pid_of() -> is_pid_alive()` en `tools/dayz_mcp/orphan_guard.py:434-435`, que es la parte que decide si se preserva una instancia viva. Por tanto, aunque el test pasara, podria ocultar bugs en la liveness real; de hecho no cubriria OG-P1-02.

Ademas, en esta ejecucion el test ni siquiera pasa. La suite venv fallo en `tests.test_port_reclaim.ReclaimIntegrationTest.test_dead_parent_orphan_is_reclaimed_end_to_end`: el log fue `RECLAIM: pid=... image=python.exe parent_pid=... parent_alive=False is not a reclaimable orphan; preserving E4 lock`. La causa observable es que `command_line_of()` no recupera cmdline: `command_line_of(os.getpid()) -> None`; `_wmic_command_line()` depende de `wmic process where ... get commandline /value` (`tools/dayz_mcp/orphan_guard.py:223-238`) y `_powershell_command_line()` de `Get-CimInstance Win32_Process` (`tools/dayz_mcp/orphan_guard.py:241-259`), que en esta sesion fallan.

Modo de fallo concreto: si en produccion el reclaim necesita recuperar un orphan legacy, llega a `cmdline = get_cmdline(pid) or ""` (`tools/dayz_mcp/orphan_guard.py:433`), no encuentra `dayz_mcp`, y preserva E4. Eso es fail-closed para seguridad, pero el feature no recupera el puerto. Combinado con C1, el producto queda sin proteccion real frente al caso que motivo el hardening.

### OG-P2-01 - `WaitForSingleObject` no distingue exito de fallo

`wait_for_parent_exit()` llama a `_k32.WaitForSingleObject(handle, _INFINITE)` (`tools/dayz_mcp/orphan_guard.py:121`), ignora el valor de retorno y cierra el handle (`tools/dayz_mcp/orphan_guard.py:122`). `_watch_loop()` ejecuta `on_parent_death()` despues de cualquier retorno (`tools/dayz_mcp/orphan_guard.py:311-313`), y en el server ese callback acaba en `os._exit(0)` (`tools/dayz_mcp/server.py:551-558`).

La API puede devolver `WAIT_FAILED`; en ese caso el codigo actual lo interpreta como muerte del parent y termina un server sano. No es el camino normal con un handle de proceso valido, por eso queda como WARN/P2, pero el fail-safe esta incompleto.

### OG-P2-02 - `netstat` elige primer PID por puerto, no por endpoint E4

El bind E4 real intenta `("127.0.0.1", port)` en `tools/dayz_mcp/loopback.py:420` y reintenta lo mismo en `tools/dayz_mcp/loopback.py:430`. En cambio, `listener_pid_for_port()` recorre `netstat -ano -p TCP`, acepta cualquier linea con estado `LISTENING` (`tools/dayz_mcp/orphan_guard.py:201`), solo comprueba que la local address termine en `:<port>` (`tools/dayz_mcp/orphan_guard.py:203-204`) y devuelve el primer PID (`tools/dayz_mcp/orphan_guard.py:206`).

Modo de fallo concreto: hay varias filas LISTENING para el mismo puerto en direcciones/familias distintas, o una fila wildcard/IPv6 aparece antes que la fila que realmente explica el `EADDRINUSE` de `127.0.0.1:<port>`. El reclaim puede evaluar y matar el primer PID aunque no sea el bloqueo que acaba de sufrir `_bind_exclusive()`. Con el discriminador debil de OG-P1-01, eso puede convertirse en kill incorrecto; si no, puede simplemente no recuperar el puerto.

## Lo que NO pude verificar offline

- Gate in-vivo con Cowork/Claude Code real: no lance Claude Code ni el juego, por boundary explicito.
- Arbol de procesos real bajo la entrada registrada actual de `.claude.json`; use evidencia local de `pyvenv.cfg`, tests y BUG-032.
- Comportamiento bajo PID reuse real de Windows y carreras de `netstat`/ToolHelp bajo carga.
- Localizacion/variantes exactas de `netstat` en hosts distintos; no lo uso como hallazgo principal.

## Proximo paso recomendado

No marcar `orphan-proof` como cerrado. Primero corregir los P1: identidad exacta del server antes de matar, parent desconocido como no-reclaimable, cobertura real de C1, y gate de reclaim que pase en el host objetivo sin inyectar la liveness critica. Despues ejecutar suite venv y un gate in-vivo especifico para la topologia `Claude Code -> launcher -> server`.
