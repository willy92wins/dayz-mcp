## HECHO

P-I6. `tools/tests/fence_helpers.py:153-192` `_coordinator_active_owner` llama a la operación pública `coordinator.status(observer)` (`:169`), que entra en `_condition` y ejecuta `_expire_due()`. Lee `owner.state == "active"`, `purpose == "lifecycle"`, `lease_id` y `client.session` del payload de `_public_lease_locked`. Nunca `coordinator._active`. Con el lease vencido `status` devuelve `owner=None` y `_resolve_run_owner` (`:125-150`) registra RUNNING_IDLE sin dueño (`:94-105`).

P-I7. `tools/tests/test_instance_fence.py:893-946` `_compose_outcomes` / `_stmt_outcomes` / `_sequence_outcomes`: cada sentencia produce el conjunto `{fall, retire, bad}`; un `if` es la unión de las ramas (sin `else`, `{fall}`); la secuencia solo continúa por `fall`, así que una rama `retire` no borra una rama `fall`. `_exited_path_violations` (`:987-991`) viola si el conjunto final no es exactamente `{retire}`. El mutante split-branch es el tercer control permanente: `test_every_exited_path_retires_bindings` `:732-736` + `_apply_split_branch_exited_mutant` `:1041-1055`.

P-I8. `tools/tests/test_session_e2e.py:233` `self.assertIs(result.get("dispatchable"), True, result)` justo después del `ok` en `adopt_run`.

P-I9. `owner=` explícito se contrasta con el titular activo: `fence_helpers.py:143-148` + `_explicit_owner_matches_active` `:195-207` (lease_id; session contra el payload truncado). Si no hay titular, se acepta. Docstring de `bind_both_peers` `:21-29` y de `_register_manifest_run` `:69-74`: ese fixture NO puede liberar ni adoptar después. `tools/tests/test_daemon.py:95-100` sigue usándolo y no libera.

`test_session_e2e.py` se reconcilia y no se commitea (R5). Producto `tools/dayz_mcp/` intacto.

## GATE

```
PRODUCTO-CONGELADO OK (63 ficheros)
```

```
========================================================================================================
[PASS ] T1a-DUENO-ACOPLADO con A titular, el run registrado es de (A, lease-A)
          state='RUNNING' owner=('A', 'lease-A') -- un dueno que no es el titular del lease es un run que liberar A no cerca
[PASS ] T1b-PROCESOS-OWNED-GUARD-REAL los procesos registrados son nuestros para el guard real
          buckets={'owned': [38536], 'gone': [], 'foreign': [], 'unknown': []} reason=None pids=[38536] -- un run cuyos procesos el guard real no reconoce no es adoptable ni reapeable como en produccion
[PASS ] T1c-SIN-LEASE-NO-INVENTA-DUENO sin lease activo ni owner explicito no hay run con dueno desconocido
          raised=None run=('RUNNING_IDLE', None, None) -- un dueno que ningun test controla es un estado imposible en produccion
[PASS ] T2-MUTANTE-BYPASS-EXITED el detector muere con una rama que persiste EXITED sin retirar
          rc=1 -- rc 0 significa que 'todo camino EXITED retira' se satisface con una rama que persiste y retorna sin retirar; cola: "dmin_reconcile:3333'\n\n- ['admin_reconcile:3333']\n+ [] : EXITED path persists or returns before retiring: admin_reconcile:3333\n\n--------------------------------------------
[PASS ] T2b-MUTANTE-ORDEN-INVERTIDO el detector muere si _commit_retirement persiste antes de retirar
          rc=1 -- el orden retirar->persistir tiene que ser observable; cola: 'ault:2803; repair_manifest_recovery:2892; _reap_run_locked:2976; admin_reconcile:3428\n\n----------------------------------------------------------------------\nRan 1 test in 0.084s\n\nFAILED (failures=1)\n'
[PASS ] T3-MUTANTE-RAMA-SIN-LIFECYCLE el test without_lifecycle muere si la rama lifecycle-is-None lanza
          rc=1 -- rc 0 significa que el test que promete 'without lifecycle' ya no pasa por esa rama; cola: 'p_creation_time\n    raise AssertionError("mutant: lifecycle-is-None branch")\nAssertionError: mutant: lifecycle-is-None branch\n\n----------------------------------------------------------------------
[PASS ] T4-NOMBRE-RELEASE el test del release se llama por lo que prueba (cerca hasta adoptar)
          retires_bindings=0 fences_bound_binding_until_adopt=1
[PASS ] T5-LEASE-CADUCADO-NO-ES-DUENO un lease vencido no se convierte en dueno RUNNING
          ttl=120.0 raised=None run=('RUNNING_IDLE', None, None) -- un dueno cuyo lease ya vencio es un run que ningun release va a cercar: el helper leyo el titular sin pasar por el coordinador
[PASS ] T6-MUTANTE-RAMA-DIVIDIDA el detector muere si solo una rama del if retira y la otra persiste y retorna
          rc=1 -- rc 0 significa que una rama que retira tapa a la rama que persiste EXITED y retorna sin retirar: pertenencia agregada, no camino; cola: "dmin_reconcile:3333'\n\n- ['admin_reconcile:3333']\n+ [] : EXITED path persists or returns before retiring: admin_reconcile:3333\n\n-----------------------
[PASS ] T7-MUTANTE-DISPATCHABLE-FALSE el e2e que adopta muere si adopt publica dispatchable False
          rc=1 -- rc 0 significa que el e2e adopta y sigue verde aunque la respuesta declare que el run no despacha (C4/P-I2 sin observar); cola: "^^^^^^^^^^^\nAssertionError: False is not True : {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': False}\n\n---------------------------------
========================================================================================================
ORACULO-TESTS: PASS=10 FAIL=0 UNMET=0 de 10
ORACULO-TESTS-VERDE
```

```
Ran 2524 tests in 197.548s
rojos = permitidos (2)
SUITE-COMPLETA OK
```

## CONTROL POSITIVO

TTL (reloj `+= SESSION_TTL_S + 1` antes de `bind_both_peers`; misma construcción que T5):

```
ttl=120.0 state='RUNNING_IDLE' owner=(None, None)
coordinator.status owner=None
```

Split-branch sobre copia temporal (`admin_reconcile` + mutante `__mutant_split_branch__`; unittest del detector):

```
test_every_exited_path_retires_bindings (tests.test_instance_fence.ExitedBindingInvariantTest.test_every_exited_path_retires_bindings) ... FAIL

======================================================================
FAIL: test_every_exited_path_retires_bindings (tests.test_instance_fence.ExitedBindingInvariantTest.test_every_exited_path_retires_bindings)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...\tools\tests\test_instance_fence.py", line 713, in test_every_exited_path_retires_bindings
    self.assertEqual(
        violations,
        [],
        "EXITED path persists or returns before retiring: "
        + "; ".join(violations),
    )
AssertionError: Lists differ: ['admin_reconcile:3333'] != []

First list contains 1 additional elements.
First extra element 0:
'admin_reconcile:3333'

- ['admin_reconcile:3333']
+ [] : EXITED path persists or returns before retiring: admin_reconcile:3333

----------------------------------------------------------------------
Ran 1 test in 0.091s

FAILED (failures=1)
```

`dispatchable: False` sobre copia temporal (payload de adopt; e2e que adopta):

```
test_parallel_reads_and_mutations_are_fifo_non_interleaved (tests.test_session_e2e.SessionE2ETest.test_parallel_reads_and_mutations_are_fifo_non_interleaved) ... FAIL

======================================================================
FAIL: test_parallel_reads_and_mutations_are_fifo_non_interleaved (tests.test_session_e2e.SessionE2ETest.test_parallel_reads_and_mutations_are_fifo_non_interleaved)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...\tools\tests\test_session_e2e.py", line 261, in test_parallel_reads_and_mutations_are_fifo_non_interleaved
    await self.adopt_run(runtime_a, acquired_a["lease_token"])
  File "...\tools\tests\test_session_e2e.py", line 233, in adopt_run
    self.assertIs(result.get("dispatchable"), True, result)
AssertionError: False is not True : {'ok': True, 'run_id': 'test-run', 'state': 'RUNNING', 'dispatchable': False}

----------------------------------------------------------------------
Ran 1 test in 0.269s

FAILED (failures=1)
```

## LO QUE NO PUDE VERIFICAR

- `ClientIdentity.public_payload` trunca `session` a 12 caracteres. T1a usa `session_id="A"`, así que el round-trip del titular implícito por `status()` no se midió con un `session_id` más largo que 12. El contraste por `lease_id` (P-I9) cubre el `owner=` explícito; el camino implícito guarda lo que publica el coordinador.
- Los hilos de cleanup que `_expire_due()` arranca al caducar el lease (T5 llama a `status` antes de registrar el run; no había run que reaper).
- El sandbox de Codex donde su suite completa salió roja (47 ERROR + `psutil.AccessDenied`). Aquí: Ran 2524, rojos = los 2 permitidos.
- Ejecución in-game / DayZDiag. Solo tests y gates.

## DISPUTAS

Ninguna. Los tres criterios del oráculo (T5, T6, T7) eran reproducibles y el producto congelado no hizo falta tocarlo.
