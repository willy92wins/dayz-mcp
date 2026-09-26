## HECHO

P-I1 `tools/tests/fence_helpers.py:15` `bind_both_peers(..., *, owner=)` — el dueño lo pone el test. `_resolve_run_owner` (`:107`) lee `owner=(session_id, lease_id)` o el lease activo del coordinador (`coordinator._active.client.session_id` + `lease_id`); sin ninguno no acuña `fence-fixture-owner`. `_register_manifest_run` (`:40`) registra RUNNING sólo con dueño resuelto (`:88`); sin dueño registra RUNNING_IDLE sin owner (`:76`) para que adopt encuentre el run. `_owned_process_records` (`:141`): guard con `snapshots` dict siembra identidad completa de los pids sintéticos (`:172`); `NativeProcessGuard` registra `os.getpid()` (`:178`) con `guard.snapshot(pid)`. `DaemonHttpServer` pasa owner explícito en `tools/tests/test_daemon.py:95`.

P-I2 `tools/tests/test_session_e2e.py:221` `adopt_run` vía `_control._session_call("/lifecycle/adopt")`. Tests que adoptan:
- `test_parallel_reads_and_mutations_are_fifo_non_interleaved` A `:260` (acopla el dueño) y B `:282` (P-I2, antes de `world_weather_set`)
- `test_release_cancels_only_owner_queued_commands` A `:349`
- `test_delivered_command_stays_pending_and_is_never_replayed` A `:373`
- `test_vehicle_release_cleanup_is_enqueued_exactly_once` A `:393`
- `test_pin_is_capped_at_300_and_result_clears_it` A `:408`

Ningún test de `test_client_mode`/`test_daemon` libera y sigue mutando con otro cliente sobre el daemon real.

R2 (aserción cambiada porque P-I1 acopla el dueño y el release cerca el run, C2):
- `test_release_cancels_only_owner_queued_commands` → `result["ok"]` + `command_names()==["query_player_state"]` → `ToolError: run_not_owned` + `command_names()==[]` → P-I1/P-I2
- `test_vehicle_release_cleanup_is_enqueued_exactly_once` → peer ve `[vehicle_control, vehicle_release]` → peer se queda en `[vehicle_control]` (`vehicle_release_enqueued==1` intacto; el fence no despacha) → P-I1/P-I2

P-I3 `tools/tests/test_instance_fence.py:708` `test_every_exited_path_retires_bindings` — detector de camino `_exited_path_violations` (`:960`): cada `state="EXITED"` en su bloque y los que lo encierran no puede `return` ni `manifest.replace` antes de `_retire_run_bindings` o una función del módulo que retira antes de su propio replace. Controles rojos permanentes `_assert_detector_rejects_mutant` (`:733`) sobre copia en `TemporaryDirectory`: bypass `:1008`, orden invertido `_apply_inverted_commit_order_mutant`.

P-I4 `tools/tests/test_instance_fence.py:1396` — enqueue usa el fake C1; `state.lifecycle = None` va inmediatamente antes de `record_poll`.

P-I5 `tools/tests/test_instance_fence.py:600` `test_release_owner_fences_bound_binding_until_adopt`.

## GATE

```
PRODUCTO-CONGELADO OK (63 ficheros)
```

```
========================================================================================================
[PASS ] T1a-DUENO-ACOPLADO con A titular, el run registrado es de (A, lease-A)
          state='RUNNING' owner=('A', 'lease-A') -- un dueno que no es el titular del lease es un run que liberar A no cerca
[PASS ] T1b-PROCESOS-OWNED-GUARD-REAL los procesos registrados son nuestros para el guard real
          buckets={'owned': [33472], 'gone': [], 'foreign': [], 'unknown': []} reason=None pids=[33472] -- un run cuyos procesos el guard real no reconoce no es adoptable ni reapeable como en produccion
[PASS ] T1c-SIN-LEASE-NO-INVENTA-DUENO sin lease activo ni owner explicito no hay run con dueno desconocido
          raised=None run=('RUNNING_IDLE', None, None) -- un dueno que ningun test controla es un estado imposible en produccion
[PASS ] T2-MUTANTE-BYPASS-EXITED el detector muere con una rama que persiste EXITED sin retirar
          rc=1 -- rc 0 significa que 'todo camino EXITED retira' se satisface con una rama que persiste y retorna sin retirar; cola: "dmin_reconcile:3333'\n\n- ['admin_reconcile:3333']\n+ [] : EXITED path persists or returns before retiring: admin_reconcile:3333\n\n--------------------------------------------
[PASS ] T2b-MUTANTE-ORDEN-INVERTIDO el detector muere si _commit_retirement persiste antes de retirar
          rc=1 -- el orden retirar->persistir tiene que ser observable; cola: 'ault:2803; repair_manifest_recovery:2892; _reap_run_locked:2976; admin_reconcile:3428\n\n----------------------------------------------------------------------\nRan 1 test in 0.081s\n\nFAILED (failures=1)\n'
[PASS ] T3-MUTANTE-RAMA-SIN-LIFECYCLE el test without_lifecycle muere si la rama lifecycle-is-None lanza
          rc=1 -- rc 0 significa que el test que promete 'without lifecycle' ya no pasa por esa rama; cola: 'p_creation_time\n    raise AssertionError("mutant: lifecycle-is-None branch")\nAssertionError: mutant: lifecycle-is-None branch\n\n----------------------------------------------------------------------
[PASS ] T4-NOMBRE-RELEASE el test del release se llama por lo que prueba (cerca hasta adoptar)
          retires_bindings=0 fences_bound_binding_until_adopt=1
========================================================================================================
ORACULO-TESTS: PASS=7 FAIL=0 UNMET=0 de 7
ORACULO-TESTS-VERDE
```

```
Ran 2524 tests in 198.095s
rojos = permitidos (2)
SUITE-COMPLETA OK
```

## CONTROL POSITIVO

Mutante bypass (copia temporal de `dayz_mcp/process_lifecycle.py` + `admin_reconcile` rama `__mutant_bypass__`; el test detector):

```
test_every_exited_path_retires_bindings (tests.test_instance_fence.ExitedBindingInvariantTest.test_every_exited_path_retires_bindings) ... FAIL

======================================================================
FAIL: test_every_exited_path_retires_bindings (tests.test_instance_fence.ExitedBindingInvariantTest.test_every_exited_path_retires_bindings)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...\tools\tests\test_instance_fence.py", line 713, in test_every_exited_path_retires_bindings
    self.assertEqual(
    ...
AssertionError: Lists differ: ['admin_reconcile:3333'] != []

First list contains 1 additional elements.
    First extra element 0:
'admin_reconcile:3333'

- ['admin_reconcile:3333']
+ [] : EXITED path persists or returns before retiring: admin_reconcile:3333

----------------------------------------------------------------------
Ran 1 test in 0.077s

FAILED (failures=1)
```

Mutante orden invertido (copia temporal, retire↔replace en `_commit_retirement`):

```
test_every_exited_path_retires_bindings (tests.test_instance_fence.ExitedBindingInvariantTest.test_every_exited_path_retires_bindings) ... FAIL

======================================================================
FAIL: test_every_exited_path_retires_bindings (tests.test_instance_fence.ExitedBindingInvariantTest.test_every_exited_path_retires_bindings)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...\tools\tests\test_instance_fence.py", line 713, in test_every_exited_path_retires_bindings
    self.assertEqual(
    ...
AssertionError: Lists differ: ['stop_run:2440', 'cleanup:2676', 'repair_[98 chars]428'] != []

First list contains 6 additional elements.
    First extra element 0:
'stop_run:2440'

+ []
- ['stop_run:2440',
-  'cleanup:2676',
-  'repair_recovery_fault:2803',
-  'repair_manifest_recovery:2892',
-  '_reap_run_locked:2976',
-  'admin_reconcile:3428'] : EXITED path persists or returns before retiring: stop_run:2440; cleanup:2676; repair_recovery_fault:2803; repair_manifest_recovery:2892; _reap_run_locked:2976; admin_reconcile:3428

----------------------------------------------------------------------
Ran 1 test in 0.084s

FAILED (failures=1)
```

P-I4 con la rama `if lifecycle is None` lanzando (copia temporal de `loopback.py`):

```
test_missing_creation_time_without_lifecycle_does_not_deliver (tests.test_instance_fence.Round4FenceRegressionTest.test_missing_creation_time_without_lifecycle_does_not_deliver) ... FAIL

======================================================================
FAIL: test_missing_creation_time_without_lifecycle_does_not_deliver (tests.test_instance_fence.Round4FenceRegressionTest.test_missing_creation_time_without_lifecycle_does_not_deliver)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...\tools\tests\test_instance_fence.py", line 1397, in test_missing_creation_time_without_lifecycle_does_not_deliver
    _status, poll = state.record_poll(
                    ~~~~~~~~~~~~~~~~~^
        "client", instance=minted, source_pid=record.pid
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "...\tools\dayz_mcp\loopback.py", line 2091, in record_poll
    source_creation_time = self._lookup_creation_time(source_pid)
  File "...\tools\dayz_mcp\loopback.py", line 1417, in _lookup_creation_time
    raise AssertionError("mutant: lifecycle-is-None branch")
AssertionError: mutant: lifecycle-is-None branch

----------------------------------------------------------------------
Ran 1 test in 0.011s

FAILED (failures=1)
```

E2e P-I2 ANTES de añadir `adopt_run` de B, con el dueño ya acoplado a A (A adoptó tras acquire; B mutó sin adoptar):

```
test_parallel_reads_and_mutations_are_fifo_non_interleaved (tests.test_session_e2e.SessionE2ETest.test_parallel_reads_and_mutations_are_fifo_non_interleaved) ... ERROR

======================================================================
ERROR: test_parallel_reads_and_mutations_are_fifo_non_interleaved (tests.test_session_e2e.SessionE2ETest.test_parallel_reads_and_mutations_are_fifo_non_interleaved)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:\Python314\Lib\asyncio\runners.py", line 127, in run
    return self._loop.run_until_complete(task)
  File "C:\Python314\Lib\asyncio\base_events.py", line 719, in run_until_complete
    return future.result()
  File "...\tools\tests\test_session_e2e.py", line 282, in test_parallel_reads_and_mutations_are_fifo_non_interleaved
    await runtime_b.call_bridge("world_weather_set", {}, "server", 2.0)
  File "...\tools\dayz_mcp\server.py", line 1473, in call_bridge
    raise ToolError(error)
mcp.server.fastmcp.exceptions.ToolError: remote_error

----------------------------------------------------------------------
Ran 1 test in 0.635s

FAILED (errors=1)
```

## LO QUE NO PUDE VERIFICAR

- `suite_full.sh` una sola vez (198 s, 2524 tests), no tres pasadas consecutivas.
- El rojo del e2e P-I2 se capturó una vez, antes de añadir el adopt de B; no se volvió a reproducir después (habría que quitar esa línea).
- No ejecuté `NativeProcessGuard.terminate` contra el intérprete vivo: el fixture usa `launch_acknowledged=True` / `launch_operation_id=None`, y `begin_release_owner` entonces no termina procesos (solo idle+fence). T1b sólo clasifica `owned`.
- No corrí los mutantes del detector bajo otro intérprete ni otro OS; las copias fueron `%TEMP%` en esta máquina.

## DISPUTAS

Ninguna sobre el oráculo. El brief P-I1 permite «dejar el manifiesto como estaba, o lanzar» si no hay dueño; el helper registra RUNNING_IDLE sin owner (`fence_helpers.py:76`) en vez de omitir el run, porque omitirlo hace `manifest.get` → None y adopt/C1 mueren con `binding_retired`. T1c sigue verde: no hay RUNNING con dueño desconocido.
