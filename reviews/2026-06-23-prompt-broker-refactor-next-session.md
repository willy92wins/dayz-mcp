# Prompt next-session — refactor broker/daemon DayZ-MCP (plan mode + impl)

> Generado 2026-06-23. Pega el bloque entre los marcadores en una sesión Cowork NUEVA.
> Decidido con el usuario: el modelo broker es el enfoque elegido (multi-sesión MCP on).

```
===== PROMPT INICIO =====

Sesión nueva. Proyecto DAYZ-MCP. Tarea: DISEÑAR (plan mode) y luego IMPLEMENTAR el
refactor a modelo broker/daemon que permita que VARIAS sesiones Cowork tengan las tools
`dayz-mcp` cargadas a la vez sobre un único juego DayZ. Cambio Python-only (sin tocar
Enforce/PBO si el diseño lo permite).

CARGA INICIAL MÍNIMA (lee solo esto, en este orden; no abras más todavía)
1. C:\Users\guill\ObsidianVault\AI\00_System\workflow.md — reglas de proceso (R1/R2/R11/R18/R21/R22).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md — LIVE-STATE: estado
   vivo, invariantes cerradas, BUG-033 (idle self-shutdown ya implementado).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md —
   arquitectura de 3 actores. EMPIEZA POR AQUÍ para el diseño.
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md — contrato de
   aceptación; el broker probablemente necesita una AC nueva.
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py — el
   server MCP a partir (hoy embebe el loopback en proceso).
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py — el
   loopback/broker; ya expone los endpoints HTTP que el modo cliente reusará.
7. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\orphan_guard.py —
   watchdog/reclaim/idle; el lifecycle del daemon debe integrarse aquí.

(El CLAUDE.md del proyecto y el LIVE-STATE se auto-inyectan al arrancar; re-lee igual el HANDOFF.)

CONTEXTO DEL PROBLEMA (verificado 2026-06-23 — RE-VERIFICA con grep/read; las líneas pueden
haber corrido tras los edits del idle):
- Hay UN solo juego DayZ y su mod sondea UNA URL fija: http://127.0.0.1:8765/ (la escribe
  install-mcp.ps1 en dayz_mcp.json, pollHz 5). Por eso "más puertos" NO sirve: una 2a instancia
  en otro puerto es un bridge MUERTO (el juego nunca la sondea).
- El bind es exclusivo a propósito: ExclusiveThreadingHTTPServer con allow_reuse_address=False
  (loopback.py, ~líneas 405-410). Solo una sesión gana :8765; las demás fallan el bind
  (server.py lifespan -> SystemExit(2)) y se quedan SIN tools. Ese es el dolor a resolver.
- Hoy server.py (Runtime.call_bridge / wait_for_result) llama a self.state.enqueue_command /
  take_result EN PROCESO. El loopback ya expone los MISMOS por HTTP: POST /enqueue
  (_handle_enqueue), GET /await (_handle_await), POST /result (_handle_result), GET /poll para
  el juego (_handle_poll) — todos en loopback.py.

EL DISEÑO ELEGIDO (broker/daemon — decidido con el usuario 2026-06-23):
- UN daemon standalone dueño de :8765 y único que habla con el juego, desacoplado de toda sesión.
- Cada dayz_mcp de sesión corre en MODO CLIENTE: en vez de bindear :8765, hace POST /enqueue +
  GET /await contra el daemon (misma key). Así N sesiones tienen tools a la vez.
- El daemon serializa los comandos (un mundo compartido).

OBJETIVO DE LA SESIÓN
Producir, en plan mode, un PLAN de implementación del broker; tras aprobación del usuario,
implementarlo + tests; actualizar el HANDOFF. El plan DEBE resolver explícitamente, ANTES de la
primera línea de código:
(a) Lifecycle del daemon: ¿quién lo arranca (harness/dayz-test.ps1 al lanzar el juego /
    auto-spawn lazy por la 1a sesión / launcher aparte) y cómo lo descubren las sesiones cliente?
    ¿Cómo se apaga? (reusar el idle self-shutdown: el daemon muere si no hay juego sondeando NI
    clientes activos).
(b) Compatibilidad/fallback: ¿una sesión puede seguir en modo EMBEBIDO (standalone) si no hay
    daemon, o siempre cliente? Camino de migración que NO regrese las fases 0-4 ni los gates
    R21/X.5 in-game ya pasados.
(c) Concurrencia: arbitraje en el daemon (el lock por comando ya existe en ServerState._lock);
    ¿semántica de "quién conduce" o solo first-come serializado? Coherencia cámara/captura entre
    sesiones (A mueve cámara, B captura -> riesgo). Declarar la limitación: "todas on, conduce
    una a la vez".
(d) Dónde viven en el split: el gate de versión (Runtime._version_state_for / record_poll), el
    chokepoint exec (ServerState._enqueue_exec_enforce), el lock E4 y el orphan-guard
    (watchdog/reclaim/idle) -> TODOS deben quedar en el DAEMON. El cliente solo proxya.
(e) Cambio de registración: el modo cliente cambia el comando registrado (de --port a un flag
    tipo --connect/--client); actualizar install-mcp.ps1 + re-registrar.

YA CERRADO (NO relitigar):
- "Más puertos / instancias sueltas" RECHAZADO (bridges muertos; el juego sondea una URL). El
  broker es EL enfoque elegido.
- Idle self-shutdown YA implementado + 91/91 + E2E verificado (el binario real bindeó y
  auto-liberó :8765 en 5.3s con stdin abierto y mudo). NO rehacerlo; INTEGRARLO con el daemon.
  Vive en orphan_guard.compute_idle_seconds / install_idle_watchdog + server.py
  Runtime.touch/idle_seconds + --idle-timeout (default 1800) + install-mcp.ps1 -IdleTimeoutSeconds.
- Lock E4 exclusivo SE QUEDA (el daemon bindea exclusivo).
- Single-owner del JUEGO es inherente; el broker = "muchos clientes, un juego", serializado.

REGLAS QUE APLICAN
- R1: tarea compleja -> plan mode primero. NO implementar hasta aprobación.
- R2: cite-then-verify. Toda API/path/línea citada arriba, RE-verifícala con grep/read (corrieron
  tras los edits del idle 2026-06-23).
- R18: ante ambigüedad de alcance/lifecycle/destino -> AskUserQuestion, no presuponer. (a)-(e) son
  candidatos a preguntar antes de cerrar el plan.
- R21: DayZ-MCP es infra/tooling (NO data-crítico de progreso de jugador) -> el review adversarial
  del código implementado es R21, no R9.
- R6: fail-closed — el daemon mantiene key por request, whitelist, version gate, exec chokepoint.
  No abrir nada al pasar a multi-cliente.
- R11: conclusión arriba, sin floritura.

ENTREGABLE
1. Plan en DayZ_MCP_dev\plans\2026-06-DD-broker-refactor.md, con (a)-(e) resueltos y trazados a
   una AC del product-spec.
2. Tras aprobación: implementación Python (daemon + modo cliente + integración orphan-guard/idle +
   install-mcp.ps1) con tests unittest nuevos.
3. Suite verde: python -m unittest discover -s tests -t .  desde DayZ_MCP_dev\tools con
   .venv-mcp\Scripts\python.exe (NO hay pytest). Hoy 91/91; no regresar.
4. HANDOFF LIVE-STATE actualizado.

ENTORNO / GOTCHAS
- Tests con unittest (no pytest), venv .venv-mcp. Editable install (__editable__.dayz_mcp_tools)
  -> los cambios a tools\dayz_mcp\*.py los coge una sesión nueva sin reinstalar.
- NO editar .py directo en OneDrive con bloques enormes (null bytes/truncado): edits quirúrgicos +
  read-after-write; bloques grandes vía heredoc a temp si hace falta.
- El E2E del idle se probó con un harness que lanza el binario real con stdin abierto y mudo (=
  host colgado). Reusa esa técnica para probar el daemon standalone end-to-end.
- ToolSearch (no `claude mcp list`) es el gate de readiness de las tools MCP.

PROHIBIDO en esta sesión
- Tocar el bridge Enforce / el PBO, salvo que el diseño DEMUESTRE que el juego debe sondear
  distinto (no debería: sigue sondeando un único :8765 = el daemon). Mantenerlo Python-only.
- Implementar antes de la aprobación del plan.
- Re-derivar el idle self-shutdown o bajar su default sin medir.
- Romper el gate de versión / exec chokepoint / fases 0-4 / gates R21-X.5.

MEJORAS DE SKILL (paso obligatorio del handoff)
Al cerrar, propón (sin que el usuario lo pida) qué SKILL.md/runbook/CLAUDE.md habría evitado los
errores o capturado los patrones de esta sesión, APPEND-only, citando el hallazgo. Aplica los
aprobados (skills propias del usuario son editables; plugin -> ledger skill-patches-pending.md).

Cuando el plan esté listo: el usuario decide si va a R22 (Codex revisa el plan) o R21 (review del
código ya implementado) en esta misma sesión o en otra.

===== PROMPT FIN =====
```
