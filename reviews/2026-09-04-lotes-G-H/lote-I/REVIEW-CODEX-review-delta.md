## VEREDICTO

NO es seguro integrar todavía este delta de tests: quedan 2 hallazgos ALTA y 1 MEDIA reproducidos; `product_frozen` y el oráculo están verdes, pero la suite completa queda inconclusa por 48 rojos ambientales ajenos al delta.

## HALLAZGOS

### ALTA — F-05: `bind_both_peers` convierte un lease ya caducado en dueño `RUNNING`

**Ubicación:** `../ws/tools/tests/fence_helpers.py:74-99,107-138`; mecanismo real en `../ws/tools/dayz_mcp/session_coordination.py:1733-1746,2084-2093`.

El delta lee directamente el atributo privado `_active`, sin tomar el lock del coordinador ni ejecutar `_expire_due()`:

[EXACT] Delta literal de `../ws-frozen-r1/tools/tests/fence_helpers.py` a `../ws/tools/tests/fence_helpers.py` (`fence_helpers.py:124-137`):

```diff
+    coordinator = getattr(lifecycle, "coordinator", None)
+    active = getattr(coordinator, "_active", None)
+    if active is None:
+        return None
+    client = getattr(active, "client", None)
+    session_id = getattr(client, "session_id", None)
+    lease_id = getattr(active, "lease_id", None)
+    if (
+        isinstance(session_id, str)
+        and session_id
+        and isinstance(lease_id, str)
+        and lease_id
+    ):
+        return session_id, lease_id
```

La API pública `status()` sí entra en `self._condition` y llama a `_expire_due()` (`session_coordination.py:1733-1736`); esa rutina libera `_active` cuando `time_fn() >= effective_expiry()` (`session_coordination.py:2084-2093`). El helper evita ambas cosas.

Reproducción ejecutada con el helper candidato, `SessionCoordinator`/`ProcessLifecycle` reales y reloj controlado: adquirir A, avanzar el reloj a `SESSION_TTL_S + 1`, y sólo entonces llamar a `bind_both_peers`.

[EXACT] Resultado observado:

```text
ttl=120.0 state='RUNNING' owner=('session-A', 'lease-A') expires_in=-1.0
```

**Fallo concreto:** P-I1 exige el titular **ACTIVO**. Aquí el manifiesto queda en `RUNNING` con A aunque su lease ya está vencido; el fixture evita `RUNNING_IDLE` y puede dar verde a una prueba que debería necesitar adopción. Que el lease caduque o cambie **después** de un bind válido sí pasa por el callback de cleanup cableado en `daemon.py:450-466` y por `_release_active_locked("lease_expired")` en `session_coordination.py:2088-2093`; el hueco demostrado es caducidad pendiente **antes/durante** la resolución del dueño.

**Fix sugerido:** retirar la lectura cruda de `_active`. El helper debe resolver el dueño mediante una operación del coordinador que expire bajo lock, o registrar `RUNNING_IDLE` salvo que el caller entregue una identidad capturada de una adquisición que el propio test acaba de validar. Añadir como control permanente la reproducción de TTL anterior.

### ALTA — F-06: el detector P-I3 acepta retirada en una sola rama y persistencia/retorno en la otra

**Ubicación:** `../ws/tools/tests/test_instance_fence.py:883-904,927-932,960-984`.

El veredicto de un `if` colapsa todas sus salidas a un único valor. Basta que **una** rama retire para devolver `"retire"`; `_sequence_verdict` deja entonces de inspeccionar las sentencias posteriores:

[EXACT] Delta candidato en `test_instance_fence.py:892-903`:

```diff
+        body_seq = _sequence_verdict(stmt.body, retiring)
+        if _compare_is_exited_state(stmt.test):
+            return body_seq
+        orelse_seq = _sequence_verdict(stmt.orelse, retiring) if stmt.orelse else "fall"
+        if body_seq == "retire" or orelse_seq == "retire":
+            if body_seq == "bad" or orelse_seq == "bad":
+                # A non-EXITED branch may persist; only the EXITED path matters.
+                if _compare_is_exited_state(stmt.test):
+                    return body_seq
+            return "retire"
+        if body_seq == "bad" or orelse_seq == "bad":
+            return "bad"
```

Ataqué el test con un tercer mutante distinto de sus controles permanentes de bypass y orden. Se aplicó sólo sobre una copia temporal de `process_lifecycle.py`:

[EXACT] Mutante ejecutado:

```diff
@@ def admin_reconcile(...):
+        if reason == "__mutant_split_branch__":
+            run = self.manifest.get(run_id)
+            run.state = "EXITED"
+            if run_id == "__only_path_that_retires__":
+                self._retire_run_bindings(run.run_id, "mutant")
+            self.manifest.replace(run)
+            return {"reconciled": True, "run_id": run_id, "state": "EXITED"}
```

[EXACT] Resultado del control y del mutante contra `ExitedBindingInvariantTest.test_every_exited_path_retires_bindings`:

```text
Ran 1 test in 0.037s
OK
CONTROL_RC=0
Ran 1 test in 0.046s
OK
MUTANT_RC=0
```

**Fallo concreto:** para cualquier `run_id != "__only_path_that_retires__"`, el camino persiste `EXITED` y retorna sin retirar el binding. El detector lo acepta porque la otra rama produce `"retire"`. Por tanto continúa siendo pertenencia agregada, no sensibilidad a todos los caminos, y no acredita C3/P-I3.

**Fix sugerido:** propagar un conjunto de estados por cada salida alcanzable (`fall`, `retire`, `bad`) y componer secuencias por rama; una rama `retire` no puede borrar una rama `fall`. Incorporar este mutante split-branch como tercer control permanente.

### MEDIA — F-07: el e2e adopta de verdad, pero no fija `dispatchable` en la respuesta

**Ubicación:** `../ws/tools/tests/test_session_e2e.py:221-233,280-283`; contrato producido en `../ws/tools/dayz_mcp/process_lifecycle.py:2487-2562`.

La adopción no es un atajo: llama a `/lifecycle/adopt`, comprueba `ok`, y B la ejecuta antes de `world_weather_set`. Sin embargo, el helper ignora el campo contractual `dispatchable`:

[EXACT] Delta literal de `test_session_e2e.py:221-233`:

```diff
+    async def adopt_run(
+        self,
+        runtime: server.ClientRuntime,
+        lease_token: str,
+        run_id: str = "test-run",
+    ) -> dict:
+        result = await runtime._control_with_lazy_spawn(
+            runtime._control._session_call,
+            "/lifecycle/adopt",
+            {"lease_token": lease_token, "run_id": run_id},
+        )
+        self.assertEqual(result.get("ok"), True, result)
+        return result
```

El productor calcula y publica el campo en `process_lifecycle.py:2553-2558`. En una copia temporal cambié sólo el valor publicado, sin volver a cercar el run:

[EXACT] Mutante ejecutado:

```diff
-                "dispatchable": dispatchable,
+                "dispatchable": False,
```

[EXACT] Resultado contra `SessionE2ETest.test_parallel_reads_and_mutations_are_fifo_non_interleaved`:

```text
Ran 1 test in 0.340s
OK
CONTROL_RC=0
Ran 1 test in 0.343s
OK
MUTANT_RC=0
```

**Fallo concreto:** el e2e demuestra el efecto real —B muta después de adoptar—, pero queda verde si la respuesta viola C4/P-I2 y declara `dispatchable: false`. P-I2 pidió expresamente respuesta `ok` **más** `dispatchable`.

**Fix sugerido:** añadir en `adopt_run`, inmediatamente después de `test_session_e2e.py:232`, una aserción de identidad `result.get("dispatchable") is True`; conservar la mutación posterior como prueba independiente del efecto.

### Comprobaciones sin hallazgo

- **P-I4:** `test_missing_creation_time_without_lifecycle_does_not_deliver` pone `state.lifecycle = None` inmediatamente antes de `record_poll` (`test_instance_fence.py:1391-1402`). El oráculo T3 reemplaza esa rama por `raise`; el test muere y el oráculo da PASS. La rama se ejecuta realmente.
- **P-I5 / borrados:** la única diferencia entre nombres `test_*` de ambos árboles es `test_release_owner_retires_bindings` → `test_release_owner_fences_bound_binding_until_adopt` (`test_instance_fence.py:600`). No hay otros tests borrados o renombrados.
- **`skip` / `expectedFailure`:** no se añadió ninguno en los cuatro `.py` cambiados.
- **Superficie del delta:** el censo SHA-256 de todos los `.py` deja exactamente cuatro cambios: `fence_helpers.py`, `test_daemon.py`, `test_instance_fence.py`, `test_session_e2e.py`. `test_client_mode.py` es byte a byte idéntico al congelado. Los cinco hashes entregados en el briefing coinciden.
- **Consumidores del helper:** hay 27 archivos que contienen `bind_both_peers` —el helper y 26 módulos consumidores— en ambos árboles; fuera de los cuatro archivos anteriores, su fuente no cambió. La rama `lifecycle is None` conserva el retorno temprano. La ejecución dirigida de los cuatro módulos afectados dio `Ran 177 tests in 12.028s` / `OK`.
- **`allowed_red.txt`:** SHA-256 actual `5f84e9454383d96cdb307d5217a7e5f6d006a4af6fd63fc426862e4c7c192be2`, idéntico al sello de ronda 1 en `../runs1/GATE-SEAL.txt:3`.
- **Gates solicitados:**

  | Gate | Resultado | Última línea literal |
  |---|---:|---|
  | `bash ../ws/gate/product_frozen.sh` | rc 0 | `PRODUCTO-CONGELADO OK (63 ficheros)` |
  | `bash ../ws/gate/run_oracle_tests.sh` | rc 0; PASS=7 FAIL=0 UNMET=0 | `ORACULO-TESTS-VERDE` |
  | `bash ../ws/gate/suite_full.sh` | rc 1; 47 ERROR + 1 FAIL ambientales | `SUITE-COMPLETA ROJA` |

## ASERCIONES CAMBIADAS

| Test | Antes | Después | C-item |
|---|---|---|---|
| `test_every_exited_path_retires_bindings` | Pertenencia por función y orden global de primeras líneas | `_exited_path_violations == []`, orden de `_commit_retirement` y dos mutantes permanentes | C3 / P-I3 |
| `SessionE2ETest.adopt_run` (helper nuevo usado por cinco tests) | No existía | `result.get("ok") == True`; falta `dispatchable is True` (F-07) | C4 / P-I2 |
| `test_release_cancels_only_owner_queued_commands` | El read extranjero terminaba `ok`; peer recibía `query_player_state` | `ToolError("run_not_owned")`; peer recibe `[]` | C2 |
| `test_vehicle_release_cleanup_is_enqueued_exactly_once` | Peer observaba `vehicle_control`, `vehicle_release` | Sólo observa `vehicle_control`; `vehicle_release_enqueued == 1` se conserva | C2 |

No hay otras aserciones modificadas en el delta. El cambio de nombre P-I5 no altera el cuerpo de su test.

## BACKLOG

- El `owner` explícito sólo se valida por forma y cadenas no vacías (`fence_helpers.py:111-123`); no se contrasta con el coordinador. Sí, puede desacoplarse del lease y ocultar C2 si se reutiliza en un test release→mutación. Hoy el único caller explícito es `test_daemon.py:95-98`, con `("daemon-fixture-session", "daemon-fixture-lease")`, y ese fixture no contiene esa secuencia, así que no lo elevo a fallo adicional de este lote. Conviene limitar/documentar ese escape y prohibirlo en tests C2/C4.
- La carpeta candidata contiene artefactos `__pycache__/*.pyc`; quedaron fuera del censo de producto fuente y no deben incorporarse a un commit.

## NO VERIFICADO

- No pude obtener una suite completa verde. En la repetición final, `suite_full.sh` ejecutó 2524 tests en 170.763 s y terminó con 47 ERROR y 1 FAIL de la familia ambiental anticipada: autoridad de paths/launcher nativo y `psutil.AccessDenied`. El único FAIL fue `DaemonStartupElectionProcessTest.test_run_daemon_candidate_wave_and_cross_port_publish_one_generation`; su subprocess abortó al inspeccionar `python.exe` con `psutil.AccessDenied` y terminó en `RunsBackupGateError("process_scan_incomplete")`. No lo atribuyo al delta. Los 177 tests dirigidos verdes no sustituyen este gate.
- No verifiqué estado de tracking/commit porque los árboles entregados no exponen metadatos Git utilizables; traté `test_session_e2e.py` como “sin trackear, no se commitea” según el briefing y lo incluí únicamente en la revisión byte-a-byte solicitada.
- No verifiqué un parche de corrección para F-05/F-06/F-07: esta ronda fue sólo revisión y no se modificó `../ws`.
- No actualicé memoria de proyecto ni handoff en Obsidian: el protocolo de cierre exige aprobación explícita antes de escribirlos y este encargo es no interactivo. La memoria durable de esta ronda queda limitada a este `REVIEW-CODEX.md`.
