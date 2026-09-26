## VEREDICTO

**NO es seguro integrar estos tests; ORCHESTRATOR_NEEDED.** P-I10 queda acreditado, pero P-I11 conserva un verde falso reproducible: un sitio nuevo que asigna `EXITED` mediante una variable intermedia no entra en el mapa ni exige test runtime. Los cinco SHA-256 entregados coinciden byte a byte con los artefactos revisados; el producto sigue congelado.

## HALLAZGOS

### ALTA — P-I11 no enumera el conjunto exacto de sitios que pueden asignar `EXITED`

**Ubicación:** `../ws/tools/tests/test_instance_fence.py:750-776,1712-1717`; el oráculo replica el límite en `../ws/gate/oracle_tests.py:427-445`.

El delta limita el visitante a `ast.Assign`, a un target directo `.state` y a valores `Constant`/`IfExp` cuyos brazos contengan literalmente `"EXITED"`:

[EXACT]
```diff
+        def visit_Assign(self, node: ast.Assign) -> None:
+            if stack and any(
+                isinstance(target, ast.Attribute) and target.attr == "state"
+                for target in node.targets
+            ):
+                if _value_can_be_exited(node.value):
+                    sites.add(".".join(stack))
...
+def _value_can_be_exited(value: ast.AST) -> bool:
+    if isinstance(value, ast.Constant):
+        return value.value == "EXITED"
+    if isinstance(value, ast.IfExp):
+        return _value_can_be_exited(value.body) or _value_can_be_exited(value.orelse)
+    return False
```

Los siete sitios actuales sí coinciden con el mapa: `_reap_run_locked`, `_settle_failed_launch`, `begin_release_owner.cleanup`, `repair_recovery_fault`, `repair_manifest_recovery`, `stop_run` y el `IfExp` de `admin_reconcile` (`../ws/tools/dayz_mcp/process_lifecycle.py:1734,2440,2676,2803,2892,2976,3428`). No encontré hoy `AugAssign`, `AnnAssign`, tupla, `setattr`, `dataclasses.replace` ni variable intermedia que escriban `EXITED`. El fallo es que el contrato P-I11 exige que **un sitio nuevo** rompa el gate, y eso no ocurre para sintaxis Python ordinaria.

Control ejecutado contra el propio método de test, sustituyendo temporalmente `process_lifecycle_mod.__file__` por una copia mutada:

[EXACT]
```python
def __mutant_variable_site(run):
    terminal = "EXITED"
    run.state = terminal
```

[EXACT]
```text
control_direct: successful=False failures=1 errors=0
variable_intermediate: successful=True failures=0 errors=0
```

El control directo añade `run.state = "EXITED"` y el test falla; la variante semánticamente equivalente mediante variable queda verde. El enumerador aislado también devolvió vacío para asignación por tupla, `AnnAssign`, `setattr(...)` y `dataclasses.replace(...)`.

**Fallo concreto:** una modificación futura puede introducir una función que persista `EXITED` sin añadir entrada al mapa ni prueba runtime de retirar-antes-de-persistir. `test_every_exited_site_has_a_runtime_retirement_order_test` y T10 siguen verdes, de modo que el artefacto no acredita la promesa que declara en `test_instance_fence.py:719-721`.

**Corrección sugerida:** `[DESIGN]` no prolongar otra ronda de parches AST ad hoc. El orquestador debe elegir entre (a) rebajar explícitamente este detector a heurística de deletreos conocidos, o (b) autorizar en otro lote una frontera estructural cerrada —una única operación de transición terminal— sobre la que el inventario sí pueda ser exhaustivo. Si se conserva la palabra «exacto» sin tocar producto, el detector tendría que ser conservador ante valores no literales y cubrir también `AnnAssign`, destructuring, `setattr` y reemplazos de objeto con controles negativos independientes; aun así necesitaría resolver flujo de datos para el caso de variable.

### BAJA — la ejecución directa del módulo falla porque el helper nuevo se define después de `unittest.main()`

**Ubicación:** `../ws/tools/tests/test_instance_fence.py:1708-1717`.

[EXACT]
```diff
 if __name__ == "__main__":
     unittest.main()
+
+
+def _value_can_be_exited(value: ast.AST) -> bool:
+    if isinstance(value, ast.Constant):
+        return value.value == "EXITED"
+    if isinstance(value, ast.IfExp):
+        return _value_can_be_exited(value.body) or _value_can_be_exited(value.orelse)
+    return False
```

Reproducción con el intérprete prescrito, desde `../ws/tools` y `PYTHONPATH=.`:

[EXACT]
```text
python tests\test_instance_fence.py ExitedBindingInvariantTest.test_every_exited_site_has_a_runtime_retirement_order_test
NameError: name '_value_can_be_exited' is not defined
Ran 1 test in 0.033s
FAILED (errors=1)
```

**Fallo concreto:** `unittest.main()` ejecuta la suite antes de que el intérprete alcance la definición del helper. Discovery y `python -m unittest` importan primero el módulo y no sufren el error, pero el punto de entrada directo que el propio archivo declara queda degradado.

**Corrección sugerida:** `[DESIGN]` mover `_value_can_be_exited` por encima del bloque `if __name__ == "__main__"`.

### P-I10 — sin hallazgos

El delta elimina por completo `owner=` y las inferencias del coordinador. `bind_both_peers` sólo crea `RunRecord(..., None, None, "RUNNING_IDLE", ...)` con procesos `owned` (`../ws/tools/tests/fence_helpers.py:15-24,45-87`). No encontré otra escritura de dueño en el write-set ni lecturas nuevas de atributos privados del coordinador.

Las rutas que necesitan despacho obtienen autoridad por operaciones reales del producto:

- `DaemonHttpServer` usa la `ClientIdentity` completa de `test_daemon.py:41-48`, llama al `coordinator.acquire` público y pasa ese token a `lifecycle.adopt_run` (`test_daemon.py:147-163`).
- Los tests cliente que adquieren su propio lease adoptan después con la identidad completa del `ClientRuntime` (`test_client_mode.py:130-142,209-225,785-827,859-890`).
- El E2E adopta por HTTP `/lifecycle/adopt` y comprueba `ok` y `dispatchable` (`test_session_e2e.py:223-235`).

Esto coincide con producción: `ClientIdentity` conserva los seis campos (`../ws/tools/dayz_mcp/session_coordination.py:57-63`), `acquire` es la operación pública (`:269-274`) y `adopt_run` valida lease, identidad, estado y procesos antes de persistir exactamente `client.session_id` (`../ws/tools/dayz_mcp/process_lifecycle.py:2487-2558`). El negativo `lease_required` no quedó tautológico: en `test_client_mode.py:785-827` se prueba antes de adquirir/adoptar, y sólo después se acredita la mutación. Las lecturas sin lease de `:829-946` se realizan mientras el titular de fixture mantiene un lease real; una sesión extranjera sigue sin poder mutar.

P-I11 sí acierta en la parte runtime existente: los seis tests nombrados de `../ws/tools/tests/test_process_lifecycle.py:3153-3351` inyectan fallo al persistir `EXITED` mediante `_replace_failing_exited` (`:3143-3151`) y comprueban que un resultado tardío ya se rechaza (`:3132-3141`). Los ejecuté juntos: `Ran 6 tests ... OK`. La docstring de `test_instance_fence.py:698-704,716-723` declara expresamente que no prueba todos los caminos ni intenta un CFG.

No aparecieron `skip`/`expectedFailure` nuevos, tests borrados ni renombres fuera de P-I11. El alias de `test_instance_fence.py:745-747` conserva el id sellado de T2b. La comparación recursiva del árbol de tests sólo encontró los cinco ficheros declarados y un `__pycache__` generado al ejecutar pruebas; no hay cambio fuente adicional.

## ASERCIONES CAMBIADAS

«Después» distingue el texto literal de la aserción de la precondición del fixture: varias aserciones no cambiaron, pero ahora miden un titular autorizado en vez de uno escrito a mano.

| Test | Antes | Después | C-item |
|---|---|---|---|
| `ExitedBindingInvariantTest.test_every_exited_path_retires_bindings` / nuevo alias (`test_instance_fence.py:716-747`) | Análisis de caminos, orden textual y tres mutantes. | Mapa de sitios, existencia nominal de tests runtime y heurística de orden; el id viejo llama al nuevo. | C3 sustituido por P-I11 |
| `test_enqueue_ok_when_version_matches` (`test_daemon.py:420`) | 200 con dueño escrito por helper. | Misma aserción 200; `_daemon()` adquiere y adopta con titular real. | C1 / P-I10 / P6 |
| `test_credential_retry_does_not_change_active_lease_or_run_owner` (`test_daemon.py:313`) | `IDENTITY` adquiría con el dueño del manifiesto fuera del coordinador. | Misma aserción; usa `adopt_fixture=False`, por lo que `IDENTITY` adquiere activamente. | C2 / P-I10 (elección de lease) |
| `test_client_call_bridge_round_trip` (`test_client_mode.py:201`) | Lectura `ok` con dueño inventado. | Misma aserción; el titular del fixture adquiere y adopta. | C1 / P-I10 / P6 lectura |
| `test_two_clients_one_daemon_both_get_results` (`test_client_mode.py:661`) | Dos lecturas `ok` con dueño inventado. | Mismas aserciones; titular de fixture real. | C1 / P-I10 / P6 lectura |
| `test_build_app_client_mode_routes_tools_through_daemon` (`test_client_mode.py:767`) | `query_player_state` despachaba con dueño inventado. | Misma aserción; titular de fixture real. | C1 / P-I10 / P6 lectura |
| `test_business_error_is_tool_error` y `test_timeout_*` (`test_client_mode.py:697-766`) | El comando entraba gracias al dueño inventado. | Mismas aserciones de error/timeout; entra porque el fixture posee realmente el run. | C1 / P-I10 / P6 |
| `test_pure_read_of_all_players_needs_no_lease_while_mutations_do` (`test_client_mode.py:829-857`) | Lectura `ok`; mutaciones `lease_required`, con dueño inventado. | Aserciones iguales; titular real mantiene el run y el lector sigue sin token. | C1 / P-I10 / P6 lectura; autorización de mutación |
| `test_logs_since_streams_only_new_lines_and_needs_no_lease` (`test_client_mode.py:892-946`) | Cliente sin lease sobre fixture con dueño inventado. | Misma aserción de lease nulo; `_daemon()` mantiene titular real. | P-I10 / P6 lectura |
| `test_client_acquire_stores_token_and_mutation_transports_it` (`test_client_mode.py:209-225`) | Acquire activo y mutación sobre run ya RUNNING por dueño falso. | `adopt_fixture=False`; tras acquire, `_adopt_run` exige `ok=True` y `dispatchable=True` antes de mutar. | C4 / P-I10 |
| `test_wait_stores_granted_token_and_release_clears_local_state` (`test_client_mode.py:227-245`) | Primer acquire activo; segundo queued. | Mismas aserciones; `adopt_fixture=False` evita que el primer acquire quede en cola tras un titular de fixture. | C2 / P-I10 (cola/elección) |
| `test_client_mode_session_tool_enables_existing_mutation` (`test_client_mode.py:785-827`) | `lease_required` → acquire → mutación. | Conserva el negativo; acquire seguido de `_adopt_run`, luego mutación `ok`. | C4 / P-I10 |
| `test_all_players_read_is_not_blocked_by_another_sessions_lease` (`test_client_mode.py:859-890`) | Holder adquiría con coordinador vacío; lectura ajena `ok`; mutación ajena denegada. | `adopt_fixture=False`; holder adquiere y adopta antes de las mismas aserciones. | C2 / C4 / P-I10 / P6 lectura |
| `test_connection_refused_triggers_spawn_and_retry` (`test_client_mode.py:989-1000`) | El daemon aparecía con dueño escrito. | Se construye con `adopt_fixture=True`; el reintento lee de un run adoptado realmente. | C1 / P-I10 / P6 |
| E2E `test_parallel_reads_and_mutations_are_fifo_non_interleaved` (`test_session_e2e.py:256-290`) | Ya adquiría y adoptaba tras acquire. | Sin cambio de aserción; `IntegrationDaemon` deja el fixture sin dueño y sólo los clientes adoptan. | C2 / C4 / P-I10 |
| `test_g2_normalized_identity_crosses_daemon_and_releases_lease` (`test_client_platform_alias.py:129`, fuera del write-set) | Acquire activo. | Misma aserción: el default del constructor es `adopt_fixture=False`, evitando una cola artificial. | C2 / P-I10 (elección de daemon) |

No encontré una aserción modificada sin C-item. La única afirmación nueva defectuosa es la exhaustividad del inventario P-I11, descrita como ALTA arriba.

## BACKLOG

- **Opinión sobre P-I11:** abandonar «todos los caminos» y retirar los mutantes de bypass/split/loop fue correcto tras tres fallos distintos; no los reabro como hallazgo. El problema es que el sustituto vuelve a usar la palabra «exacto» para una propiedad que su enumerador no puede cerrar. Dado el tope de rondas, mi voto es detener el bucle (`ORCHESTRATOR_NEEDED`) y retirar/degradar el gate estático, no añadir un quinto parche AST.
- La memoria durable está desactualizada: `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md:138` aún afirma que `test_every_exited_path_retires_bindings` exige retirada en todo camino. Debe reconciliarse cuando el orquestador decida P-I11; no la modifiqué porque está fuera del write-set y es de sólo lectura en esta sesión.
- `DaemonHttpServer.release_fixture_owner` (`../ws/tools/tests/test_daemon.py:165-177`) se añadió pero no tiene callsites. Es helper muerto de fixture, no una regresión conductual ni motivo para bloquear este lote.
- El alias `test_every_exited_path_retires_bindings` debe conservarse mientras T2b siga invocándolo por id. No cambia lo probado y no es hallazgo.

## NO VERIFICADO

- **Gates ejecutados en `../ws` (última línea literal):**

  | Gate | RC | Última línea |
  |---|---:|---|
  | `bash gate/product_frozen.sh` | 0 | `PRODUCTO-CONGELADO OK (63 ficheros)` |
  | `bash gate/run_oracle_tests.sh` | 0 | `ORACULO-TESTS-VERDE` (línea anterior: `ORACULO-TESTS: PASS=11 FAIL=0 UNMET=0 de 11`) |
  | `bash gate/suite_full.sh` | 1 | `SUITE-COMPLETA ROJA` |

  La suite terminó (`Ran 2525 tests in 178.364s`) con 47 `ERROR` y 1 `FAIL`. Reproduje aisladamente tres familias anticipadas por el encargo: `invalid_dayz_test_path_authority`, `native_launcher_create_failed` tras `PermissionError [WinError 5]` al resolver `C:\Users\guill`, y `psutil.AccessDenied` convertido en `process_scan_incomplete`. No las atribuyo al delta. Por ello **no verifiqué un verde integral de la suite en este sandbox**, aunque los gates de congelación y oráculo sí quedaron verdes y los tests focales revisados pasaron.
- No ejecuté DayZ ni un launcher nativo fuera del sandbox; esta revisión acredita tests Python, no comportamiento in-game.
- No pude verificar mediante Git que `test_session_e2e.py` siga sin trackear: el snapshot `../ws` no expone un repositorio Git utilizable (`fatal: not a git repository`). Sí verifiqué sus bytes y el SHA-256 entregado.
- `../ws-frozen-r3` no contiene una copia de `gate/allowed_red.txt`, así que no existe baseline directo del delta para ese fichero. El actual conserva el SHA-256 `5f84e9454383d96cdb307d5217a7e5f6d006a4af6fd63fc426862e4c7c192be2` registrado en la revisión delta anterior y el contenido de dos ids permitidos, pero no puedo demostrar desde este par frozen/current que no se tocó entre ambas instantáneas.
- No se escribió memoria durable: el vault está fuera de los roots de escritura y el único cambio autorizado por el encargo es `REVIEW-CODEX.md`.
