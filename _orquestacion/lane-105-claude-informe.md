# Informe LANE 105 (r2) — Issue #105 (bug037): probe de liveness acotado por el deadline

Estado: **COMPLETO**. Rama `feature/claude-105-lane-claude`.

### Bloque A - Archivos creados/modificados

- `tools/dayz_mcp/server.py` — `ClientRuntime.call_bridge`, rama world-read
  (lineas 2286-2298; +12/-6). El probe de liveness usa
  `probe_budget = min(LIVENESS_STATUS_TIMEOUT_S, max(0.0, deadline - self._time_fn()))`
  y se salta si `probe_budget <= 0.0`.
- `_orquestacion/lane-105-claude-informe.md` — este informe (nuevo).

### Bloque B - Resultado de los tests / verificacion

Todas las corridas con `PYTHONPATH=tools` + python del venv
(`C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe -m unittest discover -s tools/tests -p ...`).

Corridas focales (con el fix):

| patron | salida literal | veredicto |
|---|---|---|
| `test_python_backlog_fixes.py` | `Ran 9 tests in 0.995s` / `OK` | VERDE (bug037 incluido) |
| `test_mcp_host_timeouts.py` | `Ran 39 tests in 0.813s` / `OK (skipped=1)` | VERDE |
| `test_session_e2e.py` | `Ran 14 tests in 2.733s` / `FAILED (failures=3)` | ROJO preexistente (identico sin el fix) |
| `test_client_mode.py` | `Ran 53 tests in 14.192s` / `FAILED (failures=8)` | ROJO preexistente (identico sin el fix) |

Baseline (codigo de HEAD `31620b5`, fix revertido temporalmente) de las mismas:
- `test_python_backlog_fixes.py`: `Ran 9 tests in 0.999s` / `FAILED (failures=1)` —
  `test_bug037_tool_timeout_caps_daemon_startup_poll (tool_timeout=0.4)`.
- `test_session_e2e.py`: `Ran 14 tests in 2.572s` / `FAILED (failures=3)` — mismos 3 tests.
- `test_client_mode.py`: `Ran 53 tests in 14.230s` / `FAILED (failures=8)` — mismos 8 tests.

Suite completa (`-p "test_*.py"`):

- ANTES (baseline): `Ran 4173 tests in 326.528s` / `FAILED (failures=45, errors=3, skipped=58)`
- DESPUES (fix):    `Ran 4173 tests in 356.584s` / `FAILED (failures=44, errors=3, skipped=58)`

Diferencia exacta: -1 failure = `test_bug037_tool_timeout_caps_daemon_startup_poll (tool_timeout=0.4)`.
El resto de fallos/errores (44F + 3E) coincide test a test entre ambas corridas → ninguna regresion introducida.

### Bloque C - Hallazgos durante implementacion

1. **Via elegida**: fix literal del issue + "saltar el probe si no queda presupuesto"
   (el propio issue lo pide). Se evita llamar a `bridge_status_payload(timeout_s=0.0)`,
   cuyo comportamiento con timeout 0 depende de `_call`/socket y no conviene ejercitar.
   Contrato: (a) con presupuesto el probe sigue ocurriendo (tope 1.0 s);
   (b) el probe nunca excede el deadline; (c) sin snapshot se sigue al `/enqueue`,
   que con daemon caido lanza `daemon_unavailable` igual que antes (verificado por el test bug037).
2. El calculo de `probe_budget` va DENTRO de la rama world-read para no añadir una
   llamada extra a `_time_fn()` en comandos que no son world-read (relojes falsos de tests).
3. **No tocado (deuda)**: el segundo probe en `call_bridge` (rama de error de `/enqueue`
   con `version_blocked`/`lease_required`, ~linea 2331) y otros usos de
   `LIVENESS_STATUS_TIMEOUT_S` (~2432, ~2486) tampoco se acotan por el deadline. Son
   caminos de diagnostico de error, fuera del alcance del ticket; podrian exceder el
   tool_timeout hasta 1.0 s. Candidato a issue aparte.
4. **Rojo preexistente en el arbol**: 44 failures + 3 errors en la suite completa
   independientes de este cambio (p. ej. `test_client_mode` con `capabilities_unknown`,
   `test_mcp_tools`, `test_pleno_lease_and_orphans` sobre descripciones de tools,
   `test_telemetry_read_modes`, errores de schema FastMCP). Probable causa de entorno
   (worktree sin `addon/`/artefactos locales, o version de fastmcp del venv) o deuda
   posterior a #104; no se analizo a fondo por ser ajeno al ticket.
5. Entorno: los permisos del harness bloquearon crear un worktree/archivo baseline, asi
   que el "antes" se midio revirtiendo temporalmente la linea con Edit y restaurandola
   despues (diff final verificado).

### Bloque D - Handoff para la revision (Sol)

- Estado: fix aplicado, commit + push en `feature/claude-105-lane-claude`. Sin PR (por brief).
- Revisar: `tools/dayz_mcp/server.py` `ClientRuntime.call_bridge` (diff de 18 lineas);
  confirmar que saltar el probe con presupuesto 0 es aceptable (el flujo cae al
  `/enqueue`, que ya respeta `deadline`).
- Deuda conocida: punto C.3 (otros probes de liveness sin acotar) y C.4
  (44F+3E preexistentes en la suite completa en este entorno).
