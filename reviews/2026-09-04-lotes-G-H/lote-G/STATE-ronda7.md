## HECHO

P6–P8 en el write-set, sin tocar la forma de la ronda 6.

Predicado de despacho: `loopback.py:1130` `_run_is_dispatchable` (RUNNING/STARTING → sí; RUNNING_IDLE → no; el resto → retirado). Las dos ramas del enqueue lo usan vía `_enqueue_run_rejection` en `_enqueue_fence_target` (`loopback.py:1178` mutación, `loopback.py:1199` lectura). Las lecturas y el enqueue `internal=True` siguen alcanzando un run idle (N1/N2).

Mapa de tumbas: `process_lifecycle.py:895` `_activity_tombstone` bajo `_activity_lock`. Único punto que escribe sellos y consulta la tumba: `process_lifecycle.py:1045` `_seal_activity_locked`, llamado desde `_write_command_activity` (`:1068`) y `_capture_start_activity` (`:997`). Compensación de P5 después del `manifest.replace(target)` en `_persist_failed_launch_target` (`:1320`), olvido en `[inicio del intento, rollback]` (`:1092`).

Orden de la foto: `process_lifecycle.py:2718` `_take_box_snapshot` — revisión → manifiesto → actividad, con el comentario «The seal must be a lower bound of the data it certifies».

Código de rechazo nuevo: `run_not_owned` (`loopback.py:1148`), distinto de `binding_retired`. Hint en `loopback.py:96` / `_fence_reject_response` (`:1153`): «Adopt it with dayz_test_run mode=client run_id=...». Cola del antiguo dueño vaciada por `drain_pending_for_run` (`loopback.py:1085`) desde `release_owner`, la rama reconocida de `begin_release_owner` y `admin_reconcile` con supervivientes. El binding sigue BOUND; `adopt_run` rehabilita el mismo.

## GATE

No corrí `gate/run.sh` ni `gate/suite.sh`. Cada invocación de Shell (incluida la de subagentes) muere en PreToolUse antes de ejecutar nada:

```
Rejected: Hook blocked with message: --: eval: line 1: syntax error near unexpected token `&'
--: eval: line 1: `$OutputEncoding = [System.Text.Encoding]::UTF8; Get-Content -LiteralPath '...' -Raw | & { $input | powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\guill\.claude\hooks\launch-ledger.ps1" -Mode Pre }'
```

Lo mismo con `prime-agent-skills-gate.ps1` y `gpu-lease-gate.ps1`. El oráculo y la suite acotada no produjeron salida. No hay `ORACULO-VERDE` ni `SUITE-ACOTADA OK` que pegar.

## CONTROL POSITIVO

Los tests nuevos están escritos. No pude ejecutarlos (mismo bloqueo de Shell), así que no hay traceback de unittest. Lo que cada uno afirma, y por qué era rojo antes:

- (A) `test_acknowledged_begin_release_owner_rejects_world_spawn` — `test_process_lifecycle.py:2165` `self.assertEqual(payload.get("error"), "run_not_owned")`. Antes: `begin_release_owner` reconocido deja RUNNING_IDLE y `world_spawn` entra 200.
- (B) `test_admin_reconcile_with_survivors_rejects_world_spawn` — `test_process_lifecycle.py:2187` el mismo assert. Antes: reconcile con supervivientes publica RUNNING_IDLE y el binding sigue despachando.
- (C) `test_release_owner_drains_pending_queue_and_adopt_rehabilitates` — `test_process_lifecycle.py:2203` `self.assertEqual(state._bound_queues.get(INST_SERVER), [])` (vaciado); `:2206` poll `commands: []`; `:2208` enqueue idle `run_not_owned`; `:2213` tras `adopt_run` vuelve 200. Antes: la cola del spawn previo sobrevivía al release y el enqueue idle era 200.
- (D) `test_credit_during_failed_launch_wait_is_tombstoned_post_rollback_kept` — `test_process_lifecycle.py:2256` `self.assertEqual(row["activity_state"], "unknown")` tras crédito dentro de `launched.wait`; `:2267` crédito posterior al rollback es `recent`. Antes: `_forget_attempt_activity` corría antes del wait, el crédito aterrizaba después y el run restaurado publicaba `recent`.
- (E) `test_basal_does_not_land_at_or_before_tombstone` — `test_box_occupancy.py:1194` `self.assertEqual(row["activity_state"], "unknown")`. Antes: P3 hacía `pop` sin tumba y `_capture_start_activity` reseñaba el sello basal.

Otros nuevos (misma familia, no §2):

- `test_in_flight_writer_does_not_resurrect_stamp_after_unbound_observation` — `test_box_occupancy.py:1238` unknown (N21).
- `test_probe_cache_is_not_reused_after_revision_bump_during_list_runs` — `test_box_occupancy.py:1280` `assertGreater` de sondas (N22).
- `test_exec_enforce_on_idle_run_is_run_not_owned` — `test_box_occupancy.py:1296` la rama exec_enforce del enqueue.

Preservación (ya verde antes): `test_starting_run_still_dispatches_mutations` (`test_process_lifecycle.py:2222` status 200). STARTING sigue despachando.

## LO QUE NO PUDE VERIFICAR

- Salida literal de `gate/run.sh` (N19/N20/N21/N22 y los 30 verdes previos).
- Salida literal de `gate/suite.sh`.
- Rojo-antes real de los tests nuevos (unittest no arrancó).
- Que N1 (`camera_get` sobre RUNNING_IDLE) y N2 (`vehicle_release` internal) siguen verdes tras el cerco de mutaciones.
- Que `test_failed_extend_discards_activity_credited_in_the_confirm_window` sigue verde con la compensación movida después del rollback.
- Que el AST de EXITED en `test_instance_fence.py` no exige cerco nuevo (no toqué ese fichero).
- Interacciones reales de poll del bridge más allá del helper `accredited_poll`.

## DISPUTAS

El criterio del gate no lo discuto. El andamio de esta sesión sí: PreToolUse inyecta PowerShell (`... | & { $input | powershell ... }`) en un Shell bash y `eval` aborta en el `&` antes de cualquier comando. Sin esa ejecución no hay evidencia de ORACULO-VERDE. El producto está en el write-set; falta el sello del orquestador al recibir.

Lecturas sobre RUNNING_IDLE no se rechazan: N1-ATRIBUCION y `test_inherited_generation_becomes_recent_via_bound_enqueue` exigen 200 en `camera_get` sobre un run sin dueño. El cerco de P6 aplica a mutaciones no internas. Si el oráculo midiera lecturas, chocaría con esos verdes. No es un defecto del ledger de N19 (mide `world_spawn`).
