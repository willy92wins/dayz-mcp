# Prompt R21 (mitad Codex) — review adversarial orphan-guard DayZ-MCP

Patrón: R21 code/infra review (código implementado, no-data-crítico). Read-only, verdict + findings.
Mitad Claude = workflow propio (skill multi-ángulo). Consolidación posterior por Claude (receptor).

```
===== PROMPT INICIO =====

Tarea: R21 (revisión adversarial, SOLO-LECTURA) del feature "orphan-proof" de DayZ-MCP
recién implementado por Claude. Adversarial = intenta demostrar (a) que el reclaim MATA un
proceso que no debe o RELAJA el lock E4, (b) que el watchdog NO protege en producción, y
(c) que algún test da un PASS FALSO. NO arregles nada: solo hallazgos. R21/R2/R7 de tu
AGENTS.md aplican a esta sesión.

## Carga inicial obligatoria (en orden, completos)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py
   (módulo NUEVO bajo review: process primitives Win32/ctypes + watchdog Part 1 + reclaim Part 2).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py
   (reclaim enganchado en `_bind_exclusive`/`create_http_server` ~:413-446; lock E4
   `ExclusiveThreadingHTTPServer` ~:420 con `allow_reuse_address=False` que NO debe relajarse).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py
   (watchdog instalado en `run()`; `_watchdog_exit` ~:551-569).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_parent_watchdog.py
   (tests watchdog: decisión + install inyectado + integración real).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_port_reclaim.py
   (tests reclaim: discriminador unit + try_reclaim_port inyectado + integración real con squatter).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md
   (bloque LIVE-STATE: invariante E4 "ExclusiveThreadingHTTPServer (NO tocar allow_reuse_address)").

CONSULTAR SOLO SI UN HALLAZGO LO REQUIERE:
- C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md (BUG-032 = este hardening + el hallazgo C1).
- venv python para checks dinámicos (read-only): C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe
  Suite: en `...\DayZ_MCP_dev\tools` → `.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v`

## Contexto que debes ATACAR (afirmaciones del implementador — cuestiónalas, no las asumas)

- Dos mecanismos acoplados ("orphan-proof"), Windows, stdlib-only:
  1) WATCHDOG (Part 1): captura `os.getppid()` en `run()`, abre handle ctypes
     `OpenProcess(SYNCHRONIZE)` y bloquea un hilo daemon en `WaitForSingleObject(INFINITE)`;
     al morir el parent hace `runtime.stop_loopback()` best-effort + `os._exit(0)`. Fail-safe:
     si `OpenProcess` falla → loggea "watchdog disabled" y deja correr el server normal.
  2) RECLAIM (Part 2): antes del bind exclusivo E4, si el bind da EADDRINUSE, mata SOLO un
     huérfano que cumpla LAS 4 condiciones (`should_reclaim_listener`): listener es python +
     cmdline contiene `dayz_mcp` + cmdline tiene `--port <ese_port>` exacto + parent del
     listener MUERTO; luego reintenta el bind exclusivo UNA vez. Nunca mata el proceso actual
     ni una instancia viva.
- Cmdline real del server registrado (`.claude.json`): `<venv python.exe> -m dayz_mcp --keyfile <k> --port 8765 --require-version`.
  OJO: el path del venv contiene "DayZ_MCP_dev" → en minúsculas YA incluye la subcadena "dayz_mcp",
  así que la condición "cmdline contiene dayz_mcp" es prácticamente siempre cierta por el path.
- HALLAZGO C1 ya declarado (NO resuelto, marcado para review): el `python.exe` del venv es un
  REDIRECTOR (pyvenv.cfg `executable=C:\Python314\python.exe`; lanzarlo crea 2 procesos vivos
  launcher A → base python B). Topología real: Claude Code → launcher A → server B. El watchdog
  vigila a A (el parent inmediato de B). Prueba offline del implementador: al matar abruptamente
  al abuelo (análogo a Claude Code), A SOBREVIVE (solo espera a B) → el watchdog NO dispara; y el
  reclaim de la sesión siguiente vería el parent A vivo → tampoco recuperaría. Conclusión del
  implementador: el mecanismo es correcto-según-spec pero su eficacia real en producción está SIN
  confirmar. → ATACA esto: ¿es peor? ¿hay un caso donde sí funcione/no? ¿el reclaim aun así ayuda
  en huérfanos legacy/SIGKILL? ¿el implementador exageró o minimizó?
- Afirma: E4 intacto (`allow_reuse_address` sigue False), instancia VIVA nunca matada (R6
  fail-closed), suite venv 59/59 + stdlib 43/43 (1 integration skip "seguro" bajo python no-launcher),
  firmas ctypes/Win32 verificadas en runtime (R2).

## Dimensiones de la revisión (adversarial — cada una exige un modo de fallo, no un "se ve bien")

1) SEGURIDAD DEL RECLAIM (la clave): demuestra que mata lo que NO debe. Instancia viva mal
   clasificada como muerta; proceso ajeno con "dayz_mcp" en el path/cmdline + mismo `--port`;
   PID-reuse del parent; colisión de puerto en `_cmdline_targets_port`; `listener_pid_for_port`
   devolviendo PID equivocado (IPv6, varias filas LISTENING, netstat en locale no-inglés);
   dirección conservadora de `is_pid_alive` (acceso-denegado→vivo) inconsistente entre ramas.
2) E4 / fail-closed: ¿el reclaim o el retry-once relajan el bind exclusivo o enmascaran un
   EADDRINUSE que debería propagarse? ¿`allow_reuse_address` sigue False en todo el camino?
3) WATCHDOG + C1: ¿dispara por error y mata un server SANO? ¿`on_parent_death` (stop_loopback +
   os._exit) corre concurrente con el shutdown normal del lifespan (doble close / race sobre
   `runtime.loopback`)? ¿el fail-safe realmente NO hace os._exit? ¿leak del handle del parent?
   ¿el watchdog aporta protección REAL o es teatro dada la topología launcher (C1)?
4) PRIMITIVAS ctypes (R2): argtypes/restype de OpenProcess/WaitForSingleObject/CloseHandle/
   TerminateProcess/CreateToolhelp32Snapshot/Process32First/Next; struct `_PROCESSENTRY32`; leaks
   de HANDLE en TODAS las ramas; manejo de INVALID_HANDLE_VALUE; parsing frágil de `wmic /value`
   y del fallback PowerShell; truncado de handle/puntero en 64-bit.
5) TESTS — false PASS / tautología / vacuidad: ¿el integration de reclaim pasa por la VÍA REAL
   (netstat→TerminateProcess) o por otro motivo? ¿el skip del watchdog-integration oculta un fallo?
   ¿los tests con fakes inyectados son tautológicos? ¿cleanup en `finally` robusto (sin huérfanos)?
   ¿la suite verde demuestra que el FEATURE funciona dado C1, o solo que el código corre?
6) SCOPE/REGRESIÓN (R7): ¿rompe el shim `mcp_server.py` (re-exporta create_http_server), el
   `main()` standalone de loopback, `test_instance_lock`, o el baseline? ¿`reclaim_orphans=True`
   por defecto cambia comportamiento de callers (latencia, matar en otros tests)? ¿ciclo de import
   loopback→orphan_guard? ¿se preserva el guard non-loopback? ¿algo toca las 11 tools / bridge
   Enforce / install-mcp.ps1 / chokepoint versión-exec?

## Criterio de severidad (NO inflar)

- FAIL (P1, bloqueante): mata proceso equivocado / relaja E4 / crashea un arranque sano /
  false PASS de seguridad / pérdida funcional.
- WARN (P2): degradación, leak de handle, edge case real no cubierto, race no fatal.
- NIT (P3): higiene, portabilidad, naming.
Cada hallazgo cita `file:line` REAL y verificado (R2: lee el archivo antes de afirmar que una API
existe/falta o que hay un bug). Sin `file:line` verificado, el hallazgo no cuenta.

## Boundaries

- SOLO-LECTURA: hallazgos, NO fixes. NO modifiques NINGÚN archivo de código. NO reescribas el
  módulo. NO redesign (no propongas reescrituras; propón el fix mínimo en 1 línea por hallazgo).
- NO toques ni audites: las 11 tools y sus validaciones, el bridge Enforce
  (MCPMessages.c/MCPBridge.c/MCPClientBridge.c), install-mcp.ps1, el chokepoint versión/exec.
- NO re-decidas la semántica E4 frente a instancias VIVAS (es invariante cerrada): solo verifica
  que el reclaim la PRESERVA.
- El gate in-vivo (Cowork real) NO es tuyo: no intentes lanzar Claude Code ni el juego.

## Output esperado

Un archivo: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-23-orphan-guard-review-codex.md
con:
- Resumen ejecutivo: VEREDICTO (SOUND / SOUND-with-fixes / UNSOUND) + 2-3 líneas.
- Matriz de hallazgos: ID | file:line | severidad | resumen | fix de 1 línea.
- Hallazgos detallados (uno por ID, con el escenario/modo de fallo concreto).
- "Lo que NO pude verificar offline" (lo que queda al gate in-vivo).
- Próximo paso recomendado.

Y APPEND (con timestamp + status=open) una fila a la tabla de
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md
con formato: | Fecha | Review | Veredicto | FAIL/P1 | WARN/P2 | NIT/P3 | Status |

===== PROMPT FIN =====
```
