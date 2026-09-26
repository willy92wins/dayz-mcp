# GAUNTLET r2 — cierre definitivo tickets (2026-09-26)

Mandato: continuar la iteracion hasta que los 3 tickets queden resueltos con OK; al terminar, commit y push.

## Rondas r2 (iteracion post-gauntlet)

### #105 (bug037, unico fix de PRODUCCION)
- Lane: `feature/claude-105-lane-claude` @ `da22937` — `call_bridge` acota el probe de liveness por el deadline restante (`probe_budget = min(LIVENESS_STATUS_TIMEOUT_S, max(0.0, deadline - now))`), skip con presupuesto 0 (server.py:2284).
- Tests: repro bug037 pasa con 0.4 y 2.0; suite completa 4174: 45F/3E -> 44F/3E (solo baja el bug037, cero regresiones, comparacion test a test).
- Revision Sol r2: APPROVE (repro verificado por el revisor en ambos blobs).

### #102-r2 (deuda C.4: shims standalone)
- Lane: mismo `feature/claude-102-lane-claude` @ `5829ce8` — cabecera `_TOOLS_DIR` estandar en test_arg_contract_hash.py y test_playbook_reload.py (11 lineas aditivas, patron test_mcp_tools.py:20-23).
- Tests: arg_contract 18/18 OK con y sin PYTHONPATH; playbook_reload importa bien (residual KeyError playbook_reload = preexistente del #103).
- Revision Sol r2: APPROVE.

## Integracion final en main (cc02221 -> db6c430)
Merges --no-ff en orden 103, 102, 104, 105: cero conflictos.
Suite completa en main integrado: **4174 tests en 372.155s — FAILED (failures=5, skipped=10)**.
Baseline era 56F/5E/10S: **-51 fallos, -5 errores, -1 skipped**. Los 5 fallos restantes (telemetry x3, session_e2e release, mcp_tools description) son PREEXISTENTES en el baseline (verificados contra ut-fails-4174.txt).
PUSH: main cc02221..db6c430 en remoto.

## Cierre de issues
#102, #103, #104, #105: cerrados con comentario de veredicto+cierre cada uno (veredictos respaldados en _orquestacion/veredictos/rev-105-r2.txt y rev-102-r2.txt).

## Deuda restante (documentada, no bloquea)
- 3 probes de liveness sin acotar (~server.py:2331/2432/2486) — detectados por lane 105.
- telemetry 3 de 4 (preexistente), session_e2e release y mcp_tools description (preexistentes).
- C.1 rediseño test e2e; ficha GamePeer adicional en tools/_session_coordination/e2e_agent_sessions.py:183 (fuera de alcance #102).
