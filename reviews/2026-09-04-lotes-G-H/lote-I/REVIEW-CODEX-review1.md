## VEREDICTO

NO: no es seguro integrar estos tests; hay dos hallazgos ALTA reproducibles y el gate obligatorio `suite_full.sh` termina rojo.

## HALLAZGOS

### ALTA — F-01: el helper global inventa un dueño ajeno al coordinador y hace verde un E2E que omite el `adopt_run`

Ubicación: `../ws/tools/tests/fence_helpers.py:10-12,31-102`; consumidor concreto: `../ws/tools/tests/test_session_e2e.py:239-268`. Producción liga el dueño del run al cliente y lease autorizados en `../ws/tools/dayz_mcp/process_lifecycle.py:1917-1942`, y el release sólo cerca los runs de ese par en `../ws/tools/dayz_mcp/process_lifecycle.py:1693-1709`.

[EXACT] Diff del lote:

```diff
+_FIXTURE_OWNER = "fence-fixture-owner"
+_FIXTURE_LEASE = "fence-fixture-lease"
...
+        adder(
+            RunRecord(
+                run_id,
+                _FIXTURE_OWNER,
+                _FIXTURE_LEASE,
+                "RUNNING",
+                "fence-fixture",
+                "",
+                "",
+                "",
+                processes,
+            )
+        )
```

El estado resultante del constructor real de daemon fue:

```text
run test-run RUNNING fence-fixture-owner fence-fixture-lease
coord_active None
guard 41001 process_not_found
guard 41002 process_not_found
```

No es la línea productiva: `start_run` crea el `RunRecord` con `client.session_id` y `authority[1]`. El E2E libera A en `test_session_e2e.py:263` y deja que B mute en `:267` sin adoptar. Pasa únicamente porque el run pertenece al dueño sintético, así que liberar A no lo cerca.

Control negativo sobre `mutants/owner-coupled/tests/test_session_e2e.py:246-250`: se reemplazó sólo el dueño sintético por `runtime_a.identity.session_id` y `acquired_a["lease_id"]`, conservando producto y resto del test. Resultado:

```text
ERROR: test_parallel_reads_and_mutations_are_fifo_non_interleaved
...
test_session_e2e.py", line 272
    await runtime_b.call_bridge("world_weather_set", {}, "server", 2.0)
ToolError: remote_error
Ran 1 test in 0.513s
FAILED (errors=1)
```

Fallo concreto: la reconciliación conserva las aserciones FIFO haciendo imposible que C2 intervenga; por ello no detectaría que el cliente B muta sin rehabilitar el mismo binding mediante C4. Además, los PIDs sintéticos están muertos para el `NativeProcessGuard`, por lo que ese run tampoco es adoptable en el lifecycle real.

Fix sugerido: el fixture de lifecycle real debe recibir explícitamente identidad y lease del test, además de procesos que el guard clasifique `owned`; tras liberar A, el E2E debe adoptar con B antes de su siguiente mutación. Si el objetivo es aislar coordinación y excluir lifecycle, debe usar y nombrar explícitamente un lifecycle falso, no escribir un run imposible en el manifiesto real.

### ALTA — F-02: `test_every_exited_path_retires_bindings` agrega por función y deja pasar una rama que persiste `EXITED` sin retirar

Ubicación: `../ws/tools/tests/test_instance_fence.py:735-768`. C3 exige que toda transición terminal retire antes de persistir; el producto real centraliza el orden en `../ws/tools/dayz_mcp/process_lifecycle.py:1071-1083`.

[EXACT] Diff del lote:

```diff
+                    called = _call_func_name(node)
+                    if called:
+                        self.calls.setdefault(name, set()).add(called)
+                        if called == "_retire_run_bindings":
+                            self.direct_retire.add(name)
...
+        def _function_retires(func: str) -> bool:
+            if func in walker.direct_retire:
+                return True
+            return bool(walker.calls.get(func, set()) & retiring_names)
```

El predicado sólo demuestra que la función contiene alguna llamada a un nombre que retira; no demuestra que cada camino desde cada asignación `EXITED` alcance esa llamada antes de persistir o retornar.

Controles sobre copias temporales del producto:

```text
missing-direct: FAILED — EXITED without _retire_run_bindings: repair_recovery_fault (lines [2803])
order-reversed: FAILED — 1081 not less than 1078: _commit_retirement must retire bindings before manifest.replace
branch-bypass: Ran 1 test in 0.029s — OK
```

El mutante superviviente, en `mutants/branch-bypass/dayz_mcp/process_lifecycle.py:3360-3363`, añade una rama alcanzable a `admin_reconcile`:

```diff
+            if reason == "__mutant_bypass__":
+                run.state = "EXITED"
+                self.manifest.replace(run)
+                return {"reconciled": True, "run_id": run_id, "state": "EXITED"}
```

Fallo concreto: el test sigue verde aunque esa rama persiste `EXITED` y retorna sin `_retire_run_bindings`; por tanto no observa “every exited path” y no acredita C3.

Fix sugerido: hacer el detector sensible al flujo —por cada asignación terminal, rechazar cualquier `return` o persistencia alcanzable antes del retirement— y fijar como control rojo permanente tanto el bypass anterior como el orden invertido. Una comprobación sólo por pertenencia al conjunto de llamadas de la función no basta.

### MEDIA — F-03: el test “without_lifecycle” ya no ejecuta la rama `lifecycle is None`

Ubicación: `../ws/tools/tests/test_instance_fence.py:1170-1205`; rama productiva prometida: `../ws/tools/dayz_mcp/loopback.py:1415-1421`.

[EXACT] Diff del lote:

```diff
 def _enqueue_client_mutation(self, state: loopback.ServerState) -> dict:
+    run_id = next(iter(state._bindings.values())).run_id
+    _ensure_dispatchable_lifecycle(state, run_id)
     status, payload = state.enqueue_command(
```

`test_missing_creation_time_without_lifecycle_does_not_deliver` verifica `state.lifecycle is None` antes de llamar al helper, pero el helper instala `_BoundPeerDispatchable` antes del poll. El camino ejecutado ya no es `if lifecycle is None: return None`; es la excepción al intentar `lifecycle.guard.snapshot` sobre un fake sin `guard`.

Control negativo en `mutants/missing-lifecycle-branch/dayz_mcp/loopback.py:1415-1417`: sustituir la rama `lifecycle is None` por `raise AssertionError(...)` deja el test verde:

```text
test_missing_creation_time_without_lifecycle_does_not_deliver ... ok
Ran 1 test in 0.010s
OK
```

Fallo concreto: una regresión de la rama que el nombre promete no enrojece este test. Hay otros tests de C1 para el rechazo de enqueue sin lifecycle, pero éste ya no cubre su propio contrato de creación de identidad.

Fix sugerido: encolar con el fake exigido por C1 y poner `state.lifecycle = None` inmediatamente antes de `record_poll`, o renombrar el test a “without_guard” y añadir un test separado que ejecute realmente la rama sin lifecycle.

### BAJA — F-04: el nombre `test_release_owner_retires_bindings` afirma exactamente lo contrario del cuerpo nuevo

Ubicación: `../ws/tools/tests/test_instance_fence.py:600-640`; sustitución contractual documentada en `../ws/STATE.md:23,41-47`.

[EXACT] Diff del lote:

```diff
 def test_release_owner_retires_bindings(self) -> None:
-    run = self._mark_unacknowledged(run_id)
+    run = self.store.get(run_id)
...
-    self._assert_binding_retired(instance)
+    self.assertIn(instance, self.state._bindings)
+    self.assertEqual(self.state._bindings[instance].state, "BOUND")
```

Fallo concreto: el cuerpo sí prueba correctamente C2/C4, pero el identificador que aparece en discovery e informes dice que release retira el binding. Eso falsea el inventario semántico y deja de observar lo que su nombre promete.

Fix sugerido: renombrarlo a `test_release_owner_fences_bound_binding_until_adopt`.

## ASERCIONES CAMBIADAS

| Test | Antes | Después | C-item que la supersede |
| --- | --- | --- | --- |
| `LifecycleFenceTest.test_release_owner_retires_bindings` (`../ws-frozen-r0/tools/tests/test_instance_fence.py:578-589` → `../ws/tools/tests/test_instance_fence.py:600-640`) | Fuerza launch no reconocido y exige `_assert_binding_retired(instance)`. | Release normal: binding sigue `BOUND`; enqueue da `run_not_owned`; poll vacío; `adopt_run` devuelve mismo `run_id`, `RUNNING`, `dispatchable=True`; enqueue vuelve a 200. | C2; C4 para la respuesta de adopt. `../ws/STATE.md:23,41-47`. |
| `ExitedBindingInvariantTest.test_every_exited_path_retires_bindings` (`../ws-frozen-r0/tools/tests/test_instance_fence.py:657-700` → `../ws/tools/tests/test_instance_fence.py:708-796`) | Sólo acepta retirement directo dentro de la función que contiene `state = EXITED`. | Acepta una llamada a cualquier función del módulo que retire directamente y añade el orden de `_commit_retirement`. | C3, pero la implementación no lo acredita por camino; véase F-02. `../ws/STATE.md:17`. |

No cambió ninguna otra aserción. El censo AST encontró estos cambios sólo de fixture:

- `LifecycleFenceTest.setUp` cablea `state.lifecycle` al lifecycle real (`../ws/tools/tests/test_instance_fence.py:372-422`); las aserciones de sus otros siete tests permanecen iguales.
- `ProductionAttributionFenceTest.setUp`, los dos tests de formato de creation time, tres consumidores de `_enqueue_client_mutation`, `test_accredited_mutation...` y `test_unread_creation_time...` añaden un fake con `manifest.get`; sus aserciones permanecen iguales (`../ws/tools/tests/test_instance_fence.py:966-1003,1170-1275,1418-1429,1541-1569`). F-03 identifica la excepción semántica.
- `test_client_mode.py`, `test_daemon.py` y `test_session_e2e.py` son byte-idénticos a `../ws-frozen-r0/tools/tests/`; sus SHA-256 recibidos coinciden. El fixture compartido, no sus cuerpos, cambia su estado inicial.

Los cinco hashes recibidos coinciden exactamente. El diff recursivo sólo contiene `fence_helpers.py` y `test_instance_fence.py`. El censo encontró 168 módulos y 2.489 métodos `test_*` antes y después, sin tests borrados, añadidos ni renombrados. No se añadió `skip`, `skipTest`, `skipIf`, `skipUnless` ni `expectedFailure`; la única palabra `skip` añadida está en el docstring de `fence_helpers.py:39`.

El árbol actual no tiene sólo ocho consumidores: hay 26 módulos y 59 llamadas a `bind_both_peers`. La rama nueva `manifest.add` se activa directamente sólo donde el lifecycle real ya está cableado antes del bind: `../ws/tools/tests/test_daemon.py:92-95` y `../ws/tools/tests/test_session_e2e.py:43-46`; `test_client_mode.py` reutiliza `DaemonHttpServer` (`../ws/tools/tests/test_client_mode.py:22,122-123`). En los demás callsites, `install_bound_peer` instala primero `_BoundPeerDispatchable` conforme a C5 (`../ws/tools/dayz_mcp/loopback.py:1095-1100`) o el test conecta el lifecycle real después del helper. El `getter` evita sobrescribir un run que el propio test ya controla (`../ws/tools/tests/fence_helpers.py:48-54`), pero F-01 demuestra que los dos constructores reales sí reciben un dueño inventado.

Verificación focal fresca:

```text
Ran 177 tests in 11.358s
OK
```

Corresponde a `tests.test_instance_fence tests.test_client_mode tests.test_daemon tests.test_session_e2e`.

## BACKLOG

- El gate completo obligatorio está rojo fuera del diff: 48 `ERROR` y 1 `FAIL` nuevos, concentrados en tests de path authority, lifecycle de procesos nativos y elección de daemon; una muestra directa reprodujo `invalid_dayz_test_path_authority` y un subproceso falló con `psutil.AccessDenied`. No atribuyo esos fallos a este lote porque ninguno de los módulos que los define cambió y la suite focal de los cuatro módulos pedidos pasa 177/177. Aun así, un gate obligatorio rojo bloquea la integración.
- Últimas líneas literales de los comandos pedidos:

```text
PRODUCTO-CONGELADO OK (63 ficheros)
SUITE-COMPLETA ROJA
```

## NO VERIFICADO

- No se verificó si `allowed_red.txt` fue tocado: `../ws-frozen-r0` sólo contiene `tools/` y no ofrece una copia base de `gate/allowed_red.txt`; tampoco hay metadatos `.git` en `../ws`. La copia actual tiene SHA-256 `5f84e9454383d96cdb307d5217a7e5f6d006a4af6fd63fc426862e4c7c192be2` y dos líneas.
- No se verificó de forma independiente que `test_session_e2e.py` esté sin trackear, por la misma ausencia de metadatos Git. Sí se revisó y su hash recibido coincide; además es byte-idéntico a la copia congelada.
- No se aisló la causa común de los 48 `ERROR` y 1 `FAIL` del gate completo; sólo se confirmó que varios son reproducibles fuera de los dos ficheros cambiados y que la suite focal queda verde.
- No se recapturaron los 30 rojos originales contra los tests congelados antes de la reconciliación.
- No se lanzó DayZDiag ni un daemon productivo con procesos DayZ reales; la verificación fue estática, unittest, HTTP de fixture y mutantes sobre copias temporales.
