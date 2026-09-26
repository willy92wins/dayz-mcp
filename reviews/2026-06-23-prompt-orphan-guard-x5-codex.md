# Prompt X.5 (implementación Codex) — fixes R21 orphan-guard (gate-independientes)

Patrón: implementation handoff. Scope acotado a los fixes confirmados por la R21 dual que NO dependen
del gate in-vivo. **C1 (rediseño del watchdog) queda FUERA** — depende de la caracterización in-vivo.

```
===== PROMPT INICIO =====

Tarea: aplicar los fixes X.5 de la R21 (consolidada) al feature orphan-guard de DayZ-MCP.
Esta sesión cubre **únicamente** los 6 fixes gate-independientes listados abajo (F1-F6).
**NO toques C1** (el rediseño del watchdog para la topología launcher): depende de un gate
in-vivo aún pendiente y NO se decide aquí. R2 cite-then-verify + R7 (propaga invariantes a
todos los call-sites) de tu AGENTS.md aplican.

## Carga inicial obligatoria (en orden, completos)

1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-23-orphan-guard-r21-consolidated.md
   (la R21 consolidada: matriz de hallazgos con file:line y fix de 1 línea — es tu fuente de verdad).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py
   (módulo a editar: discriminador :374-395, try_reclaim_port :398-453, wait_for_parent_exit :114-122,
   listener_pid_for_port :180-209, _wmic_command_line :223-238, _powershell_command_line :241-259).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py
   (E4 ExclusiveThreadingHTTPServer :402-417 — allow_reuse_address=False NO se toca; el retry-once
   re-instancia esa misma clase).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_port_reclaim.py
   (tests a ajustar consistentemente con F1/F2).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\test_parent_watchdog.py
   (test a ajustar con F6a).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md
   (LIVE-STATE: invariante E4).

## Alcance acotado — F1-F6 (cada uno con su test)

- **F1 (RCL-DISC, FAIL)** orphan_guard.py:389: el discriminador `"dayz_mcp" in cmdline.lower()` es
  substring del cmdline COMPLETO (incl. el path del intérprete, que vive bajo `DayZ_MCP_dev`). Cámbialo
  a identidad EXACTA del server: exigir el token de módulo `-m dayz_mcp` adyacente (mismo estilo que ya
  hace `_cmdline_targets_port` con `--port`), NO substring de path. Helper análogo (p.ej.
  `_cmdline_runs_module(cmdline, "dayz_mcp")`).
- **F2 (RCL-FAILOPEN, FAIL)** orphan_guard.py:434-435: `parent_alive = is_alive(ppid) if ppid else False`
  es fail-OPEN. En `try_reclaim_port`, si `get_parent(pid)` devuelve None → `return False` (no reclamar;
  parent indeterminado ≠ parent muerto). Preserva el contrato fail-closed de :383-385.
- **F3 (WAITFAIL, WARN)** orphan_guard.py:121: `WaitForSingleObject` ignora el retorno → `on_parent_death`
  corre tras CUALQUIER retorno (incl. WAIT_FAILED). `wait_for_parent_exit` debe devolver/propagar el
  resultado y `_watch_loop` solo llamar `on_parent_death` si fue `WAIT_OBJECT_0`; ante `WAIT_FAILED`
  (0xFFFFFFFF) cerrar el handle, loggear y NO salir.
- **F4 (NETSTAT-EP, WARN)** orphan_guard.py:203: `parts[1].endswith(":<port>")` casa
  `0.0.0.0:port`/`[::]:port`/`[::1]:port`. Ancla al endpoint E4: exigir `parts[1] == "127.0.0.1:<port>"`
  (el bind real es `("127.0.0.1", port)`).
- **F5 (cmdline robustez, WARN)** orphan_guard.py:223-259: `_wmic_command_line` solo devuelve la 1ª línea
  tras `CommandLine=` → acumula el cmdline completo (multilínea) hasta la siguiente clave/EOF. Mantén el
  fallback PowerShell. Cuando `command_line_of` devuelva None, el reclaim ya NO matchea (fail-closed
  correcto) PERO loggea un mensaje DISTINTO ("cmdline unavailable; cannot confirm identity, preserving E4")
  para no confundirlo con "no es un huérfano nuestro".
- **F6 (tests)**: (a) test_parent_watchdog.py — añade/ajusta un caso 3-niveles G→A→B (matar SOLO G) que
  documente el comportamiento ACTUAL bajo launcher (el watchdog NO dispara) como contrato conocido-roto
  (usa `expectedFailure` o un assert explícito del estado actual + comentario "C1 abierto"); NO lo
  silencies con skip. (b) test_port_reclaim.py — añade un caso de liveness REAL: squatter `-m dayz_mcp`
  con parent VIVO → `try_reclaim_port` con helpers reales (sin inyectar is_alive) debe devolver False y
  NO matar (preserva E4). (c) actualiza el negativo "foreign process" para usar el path REAL del venv
  (`...\DayZ_MCP_dev\...python.exe -m http.server --port <p>`) → tras F1 debe NO reclamar. Ajusta el
  integration existente para que el squatter use `-m dayz_mcp` (no un `dayz_mcp` suelto) y siga verde.

## Restricciones críticas

1. **Solo stdlib** (os, sys, threading, ctypes, subprocess, socket, time, signal). NINGUNA dep nueva.
2. **E4 intacto**: `allow_reuse_address=False` NO se toca; el bind sigue `ExclusiveThreadingHTTPServer`.
3. **NO toques C1** (rediseño watchdog topología launcher), ni las 11 tools, ni el bridge Enforce
   (MCPMessages.c/MCPBridge.c/MCPClientBridge.c), ni install-mcp.ps1, ni el chokepoint versión/exec.
4. **NO improvises fuera de F1-F6.** Si algo no encaja, anótalo en el Bloque C, NO lo arregles al vuelo.
5. **R21 doble revisión NO se hace aquí** (ya se hizo; eres el implementador). No te autorrevises.
6. Caveats del entorno (CLAUDE.md proyecto): Write/Edit sobre .py en OneDrive puede truncar/meter bytes
   nulos → read-after-write verify obligatorio; `wmic` puede no existir / `Get-CimInstance` dar acceso
   denegado en sandboxes (no asumas que command_line_of funciona en CI).

## Output esperado (A/B/C/D)

- **A** — Archivos modificados (paths absolutos + tamaño/líneas aprox).
- **B** — Output LITERAL de la suite: `.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t . -v`
  desde `...\DayZ_MCP_dev\tools`. Debe seguir verde (los tests nuevos/ajustados de F6 reflejan los fixes).
- **C** — Hallazgos durante la implementación (o "Sin hallazgos"). Declara cualquier API que no se comportó.
- **D** — Handoff: estado al cierre, qué queda (C1 gated en el gate in-vivo), insertion points (file:line).

===== PROMPT FIN =====
```
