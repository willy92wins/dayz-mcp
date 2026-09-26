# f45d — `wait_for` devuelve `remote_error` desnudo: diagnóstico, decisión y plan del lote

Sesión «Vaciado de buzón MCP» (`local_32a70c44`), 2026-09-06 21:4x–22:0x. Orden de Guillermo: «habla con la
sesión ["Estado del proyecto y pendientes", LFPowerGrid] y pregúntale sobre el bug de mcp, asegúrate de que se
arregle, haz el plan y delegas implementación en grok».

## La ficha

`fb-20260906-193626-f45d` (LFPowerGrid, 19:36Z): `wait_for(condition="players_at_least", value=1, timeout_s=240)`
inmediatamente después de `dayz_test_run(mode="client", run_id=6723a52d…)` devuelve «Error executing tool
wait_for: remote_error», sin `error_code`, sin `reason`. La ficha atribuye la causa a que el cliente aún no
sondeaba (`bridge_ready=false, reason=client_not_polling`) y propone reintentar durante `timeout_s` o devolver
`client_not_polling`.

## Diagnóstico verificado (leído en el árbol y en el audit del daemon, no inferido del mensaje)

**La causa no es el cliente sin sondear: es la valla del run sin dueño, y un hueco en la lista blanca del cliente.**

1. Audit `%LOCALAPPDATA%\DayZ_MCP\audit\events.jsonl`, líneas 9617-9622 (UTC):
   - 19:07:15.997 `lifecycle_start_outcome` run `6723a52d` (cliente lanzado) → 19:07:16.132
     `session_release_finished reason=owner_release`: el propio `dayz_test_run` soltó el lease y el run pasó a
     `RUNNING_IDLE`.
   - 19:07:25.982 `session_authorized reason=read_only command=query_all_players` (la sonda de `wait_for`) y
     **ninguna fila `run_command_activity enqueue_accepted` detrás** (compárese con 19:08:23.250, donde
     `query_player_state` sí la tiene). El enqueue fue rechazado después de la autorización.
   - 19:08:16 `session_acquire` + `lifecycle_adopt` → run `RUNNING`; desde ahí todos los verbos entran. El
     «workaround» de la ficha funcionó por la adopción del run, no porque el puente pasara a BOUND.
2. Lado daemon: `tools/dayz_mcp/loopback.py:1293-1321` `_enqueue_run_rejection` devuelve `run_not_owned` para un
   run `RUNNING_IDLE` («RUNNING_IDLE stays run_not_owned at enqueue», :1301) también en lecturas (rama no-mutación
   de `_enqueue_fence_target`, :1370-1375); `:1323-1329` lo convierte en `409 {"error": "run_not_owned", "hint":
   _RUN_NOT_OWNED_HINT}` (`:99-102`: «This run has no owner (RUNNING_IDLE). Adopt the existing run before
   dispatching.»); `:3092-3117` lo pone en el cable tal cual. Contrato pineado por `tests/test_box_occupancy.py:
   1384-1392` y `tests/test_loopback.py:1137-1147`.
3. Lado cliente: `tools/dayz_mcp/server.py:150-197` `_REMOTE_ERROR_CODES` (46 códigos) no contiene `run_not_owned`,
   `run_state_unavailable` ni `enqueue_cancelled`; `:361-366` `_remote_error_code` devuelve `remote_error` para
   todo lo que no esté en la lista; `:728-760` `_public_enqueue_error` termina en `return code` sin mirar el hint;
   `ClientRuntime.call_bridge` `:1525-1539` levanta ese texto; `execute_wait_for` `:2641-2642` lo relanza intacto.
4. Control positivo ejecutado (`scratchpad/control_f45d.py`): `_public_enqueue_error({"error": "run_not_owned",
   "hint": …})` → `'remote_error'`; `run_state_unavailable` y `enqueue_cancelled` → `'remote_error'`;
   `binding_not_ready` y `queue_full` sobreviven (pero sin su hint).
5. Discriminador independiente, aportado por la sesión LFPowerGrid tras recibir el diagnóstico: entre sus dos
   llamadas a `bridge_status` de esa ventana, el contador `fence.mutation_rejects_by_code.run_not_owned` pasó de
   6 a 7 (`loopback.py:1809-1811` `_fence_reject_counts`): exactamente una sonda rechazada por la valla, la de
   `wait_for`. La sesión corrigió su propio relato a Guillermo y adopta `dayz_test_run → session_acquire_wait →
   wait_for`.

Por qué el cliente que carga no bloquea la sonda: `query_all_players` va al peer `server` (`server.py:2614-2616`) y
`Runtime.ensure_peer_allowed("server")` (`:871-887`) sólo mira el servidor, que estaba vivo. Con el run adoptado,
`players_at_least` sí espera al cliente mientras carga. La propuesta (a) de la ficha (reintentar por
`client_not_polling`) no aplica a este incidente y no se toca.

## Decisión (alcance del lote; ninguna decisión de diseño va dentro)

Arreglo de «el motivo se pierde por el camino», que es lo que la propia ficha pide:

- `_REMOTE_ERROR_CODES` gana `run_not_owned`, `run_state_unavailable`, `enqueue_cancelled` (más lo que encuentre
  un censo por AST de la ruta `/enqueue`, que queda como test permanente).
- `_public_enqueue_error` conserva el `hint` del daemon pegado al código (`"<code>: <hint>"`), sólo para códigos de
  la lista blanca y acotado (str, 1..240, sin espacios en los bordes ni saltos de línea); un código desconocido
  sigue siendo `remote_error` desnudo (control negativo `test_client_mode.py:331-370` intacto).
- `server.py:1527/:1618` comparan el código crudo (`_remote_error_code(payload)`) con `_STALE_LEASE_ERRORS`.
- Descripción de `wait_for`: una frase que nombra `run_not_owned` y la receta (`session_acquire_wait`).
- `execute_wait_for` no cambia: el `else: raise` ya entrega el texto; se pinea con test.

**No decidido aquí (de Guillermo, si quiere):** relajar la valla de run ocioso para lecturas; que `dayz_test_run`
deje el run adoptado por la sesión que lo lanzó; reintento de `wait_for` ante otros motivos. Ninguna de las tres
hace falta para cerrar f45d. Dato de Reserva (22:0x, leído por ella hoy) que pesa en la primera: la ruta de PARADA
ya trata `RUNNING_IDLE` como estado legítimo (`dayz_test_tool.resolve_stop_run` lo acepta junto a `RUNNING` y
`UNRECONCILED`, fijado en `tests/test_lifecycle_reconcile.py:350-365`), mientras la ruta de ENCOLADO lo valla
incluso para lecturas: es una inconsistencia entre dos caminos, no una política única. Y `recover_after_restart`
(`process_lifecycle.py:1006-1038`) deja un `RUNNING` con dueño en `RUNNING_IDLE` tras un reinicio del daemon, así
que el caso de esta ficha también aparece después de cada reinicio, no sólo tras un `dayz_test_run`.

## Lote

- Rama `work/f45d-enqueue-refusal-reason` desde `4e264bb` (viva); worktree `scratchpad/wt-f45d`; copia fiel
  `scratchpad/lote-f45d/ws` (8 MiB, hashes de `server.py` y `test_wait_for.py` iguales a HEAD; 45 tests de dos
  módulos verdes en la copia antes de lanzar).
- Implementa Grok (Cursor CLI, `cursor-grok-4.6-xhigh`, `runner_cursor.sh`, HOME de sandbox). Brief:
  `BRIEF-F45D.txt` en esta carpeta. Write-set: `tools/dayz_mcp/server.py`, `tools/tests/test_wait_for.py`,
  `tools/tests/test_enqueue_refusal_reaches_the_caller.py` (nuevo).
- Revisa Anthropic (Agent `general-purpose`, Fable) sobre el diff recibido; luego integración en el worktree,
  commit, suite completa en la viva tras el merge (baseline `Ran 2911, failures=2` = centinelas bd90).
- Sesiones avisadas: LFPowerGrid (`local_db605075`) con el diagnóstico corregido y el rodeo válido
  (`session_acquire_wait` antes de `wait_for`); Reserva (`local_e84a1b8e`) con el aviso de escritura sobre
  `server.py`. df53 (mode=all / testigo) sigue fuera de este lote.
