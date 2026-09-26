# Gates: lote V-server — cinco mejoras de ergonomía en superficies públicas de `server.py`

Scope: (V1, ficha ef5e) la descripción de `bridge_status` deja de enumerar seis `reason` y publica el
conjunto DERIVADO de `READY_REASONS ∪ _FENCE_BLOCK_READY.values()` en tiempo de `build_app`,
declarándolo abierto (validar por forma, nunca por lista blanca); (V2, ficha 05.5) `entities_query`
devuelve `reason: "no_player_connected"` cuando la sonda de jugadores es ok y la lista acreditada está
vacía, y sólo entonces; (V3, ficha 05.2) `wait_for` rechaza `timeout_s > 600` con
`bad_args: timeout_s must be <= 600` ANTES de cualquier sonda o lock (hoy lo recorta en silencio), y la
descripción y `tools/README-mcp.md` lo dicen; (V4, ficha 05.1) `wait_for(players_*)` espera A TRAVÉS
del arranque del servidor: una sonda que falla con EXACTAMENTE `game_not_ready:reason=server_poll_stale`
se reintenta hasta el deadline único, durmiendo fuera del lock, y la respuesta lleva `not_ready_probes`
y `last_error`; cualquier otro motivo aborta en la primera sonda como hoy; (V5, ficha 05.4) la
descripción de `action_use` dice que `action` es el nombre de clase Enforce de la acción, no el texto
visible.

**Fuera de alcance a propósito**: la región `dayz_test_stop` de `server.py` (`:3102` y su cuerpo, en
vuelo en otra sesión), `dayz_test_tool.py`, `loopback.py`, `daemon.py`, `process_lifecycle.py`,
`dayz_test_request.py` (su sha256 forma parte del contrato del bundle nativo), Enforce
(`MCPClientBridge.c`: listar acciones disponibles en `action_not_found` es trabajo in-game),
`_CLOSED_SCHEMA_TOOLS` (ninguna tool gana parámetros en este lote).

Este ledger lo escribió y lo corrió la sesión orquestadora ANTES de delegar. **No lo edites.**
`gate/` está sellado por hash y se comprueba al recibir.

Estado medido antes de delegar, en este workspace y con `bash gate/run.sh`:

```
PASS=10  FAIL=17  UNMET=0  de 27   (2026-09-04 13:30, árbol vivo HEAD 00d4303 copiado sin cambios)
PASS (invariantes: no se pueden romper):
  V1-authority-covers-everything-emitted, V1-fence-reasons-flow-and-are-published,
  V2-far-player-has-no-reason, V2-failed-probe-does-not-claim-no-player, V2-failed-query-untouched,
  V3-accepts-exactly-600,
  V4-aborts-on[client_not_polling], V4-aborts-on[no_run], V4-aborts-on[version_blocked],
  V4-aborts-on[server_poll_stale; serve]  (un mensaje que solo EMPIEZA por el motivo exacto sigue abortando)
FAIL (el producto):
  V1-desc-lists-every-reason, V1-desc-declares-open-set, V1-desc-derived-from-fence-map,
  V1-desc-derived-from-ready-reasons,
  V2-no-player-connected-named, V2-desc-names-reason, V2-wire,
  V3-rejects-above-600-before-any-probe, V3-desc-names-ceiling, V3-readme-truth, V3-wire,
  V4-waits-through-server-startup, V4-single-deadline-and-last-error, V4-sleeps-outside-the-lock,
  V4-desc-names-startup-wait, V4-e2e-never-polled-server (hoy: raised game_not_ready:reason=server_poll_stale,
  el error literal de la ficha, sobre el runtime real in-process),
  V5-desc-action-is-a-class-name
G2 en el baseline (medido 13:47): SUITE-ACOTADA OK — los doce módulos verdes (test_wait_for 15,
test_wait_for_launch_and_contract 21, test_wait_for_marker 5, test_wait_for_requires_a_live_run 3,
test_wave_fixes_20260824 16, test_weak_agent_consumer_ux 23, test_server_response_truth 14,
test_lote_m_products 14, test_instance_fence 63, test_playbook_tool 15, test_mcp_tools 45,
test_docs_truth 20 con skipped=2). Nota: los siete ficheros del par (loopback, process_lifecycle,
dayz_test_tool y sus tests + test_box_occupancy) están en este workspace con los bytes de HEAD, no
del árbol de trabajo (en vuelo, +5918 líneas sin commitear): fuera del write-set, no se miran.
```

```gates
[ ] G1: los cinco productos, medidos por el oráculo externo
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los doce módulos de test vecinos siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending

[ ] G3: la suite completa en el árbol vivo no pierde nada (la corre el orquestador al integrar)
  CHECK: cd tools && ./.venv-mcp/Scripts/python.exe -m unittest discover -s tests -t .
  EXPECT: los mismos dos rojos por nombre (centinela MCPBridge.c) y ninguno nuevo; tests nuevos coleccionados
  EVIDENCE: pending

[ ] G4: revisión de otra familia (Codex, sesión nueva) sin bloqueantes con repro
  EVIDENCE: pending
```
