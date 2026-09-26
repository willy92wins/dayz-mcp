## VEREDICTO

NO: no es seguro integrar estos tests; quedan 3 hallazgos ALTA con reproducción ejecutable y, al ser la ronda 3, el estado es `ORCHESTRATOR_NEEDED`, no otra iteración automática.

## HALLAZGOS

### ALTA — F-08: P-I6 registra como dueño la sesión pública truncada y el release del titular real no encuentra el run

**Ubicación:** `../ws/tools/tests/fence_helpers.py:153-191`; truncado productivo en `../ws/tools/dayz_mcp/session_coordination.py:97-103`; comparación exacta del manifiesto en `../ws/tools/dayz_mcp/process_lifecycle.py:846-860`.

La operación elegida sí cumple la parte de sincronización de P-I6: `SessionCoordinator.status()` entra en `self._condition` y llama a `_expire_due()` (`session_coordination.py:1733-1735`). También trata correctamente los estados transitorios: oculta el lease provisional de `_grant_inflight` (`:1736-1746`) y, si sólo queda `_releasing`, publica `state="releasing"` (`:1748-1750`), que el helper rechaza porque exige `state == "active"` (`fence_helpers.py:174-176`). El defecto está después: el helper toma `client.session` del payload público como si fuera la identidad durable completa.

[EXACT] Delta literal R2→R3 en `fence_helpers.py:180-191`:

```diff
+    lease_id = owner.get("lease_id")
+    client = owner.get("client")
+    session = None
+    if isinstance(client, dict):
+        session = client.get("session")
+    if (
+        isinstance(session, str)
+        and session
+        and isinstance(lease_id, str)
+        and lease_id
+    ):
+        return session, lease_id
```

[EXACT] El productor recorta esa identidad en `session_coordination.py:97-103`:

```python
def public_payload(self) -> dict[str, object]:
    return {
        "platform": self.platform,
        "session": self.session_id[:12],
        "started_at_utc": self.started_at_utc,
        "task_label": self.task_label,
    }
```

Reproducción fresca con `SessionCoordinator`, `ProcessLifecycle` y manifiesto reales, sobre copia temporal cuyo `fence_helpers.py` conserva el SHA candidato `b4a271e419e0773eb61969da96c5ad52c1a4c889ddc34c6173dcb9c944f4cf9f`:

```text
IMPLICIT-TRUNCATION-PROBE {'actual': 'abcdefghijkl-ACTUAL', 'lease': 'lease-A', 'stored_before': ('RUNNING', 'abcdefghijkl', 'lease-A'), 'released': [], 'stored_after': ('RUNNING', 'abcdefghijkl', 'lease-A')}
```

**Fallo concreto:** `bind_both_peers(..., owner=None)` registra `RUNNING` con `owner_session_id="abcdefghijkl"`; `release_owner("abcdefghijkl-ACTUAL", "lease-A")` compara la sesión completa por igualdad (`process_lifecycle.py:851-855`), devuelve `[]` y deja el run en `RUNNING`. El fixture puede ocultar C2 aunque el lease consultado estuviera legítimamente activo.

**Fix sugerido:** no convertir el campo público truncado en identidad del manifiesto. Con la API pública actual, la ruta implícita debe registrar `RUNNING_IDLE` o exigir al fixture una identidad completa capturada de la adquisición; si se quiere inferencia automática, hace falta una operación pública que valide/devuelva el par completo bajo lock. Añadir el probe de sesión de más de 12 caracteres como control permanente.

### ALTA — F-09: P-I9 acepta como “match” dos sesiones completas distintas que comparten prefijo

**Ubicación:** `../ws/tools/tests/fence_helpers.py:195-207`.

[EXACT] Delta literal R2→R3:

```diff
+def _explicit_owner_matches_active(
+    explicit: tuple[str, str],
+    active: tuple[str, str],
+) -> bool:
+    exp_session, exp_lease = explicit
+    act_session, act_lease = active
+    if exp_lease != act_lease:
+        return False
+    return (
+        exp_session == act_session
+        or exp_session.startswith(act_session)
+        or act_session.startswith(exp_session)
+    )
```

Reproducción fresca, manteniendo el mismo lease pero dando al helper otro `owner_session_id` con los mismos 12 caracteres públicos:

```text
OWNER-PREFIX-PROBE {'actual': 'abcdefghijkl-ACTUAL', 'wrong': 'abcdefghijkl-WRONG', 'lease': 'lease-A', 'stored_before': ('RUNNING', 'abcdefghijkl-WRONG', 'lease-A'), 'released': [], 'stored_after': ('RUNNING', 'abcdefghijkl-WRONG', 'lease-A')}
```

**Fallo concreto:** el helper acepta `abcdefghijkl-WRONG` como titular de `abcdefghijkl-ACTUAL`. Al liberar el titular real, la igualdad exacta del manifiesto vuelve a producir `released=[]`; C2 queda escondido. El hallazgo no penaliza una tolerancia por sí misma: demuestra que esa tolerancia materializa una identidad falsa y cambia el resultado observable del release.

El único caller actual con `owner=` explícito es `../ws/tools/tests/test_daemon.py:97-100`; dentro de ese fixture no hay release/adopt posterior, por lo que su uso concreto respeta el docstring. El fallo está en el contrato reusable P-I9 y queda ejecutado con un caller válido del helper.

**Fix sugerido:** eliminar la equivalencia simétrica por prefijo. Si hay titular activo, aceptar el `owner=` explícito sólo mediante una verificación pública de identidad completa; si esa verificación no existe, fallar cerrado o exigir un objeto de adquisición verificable, no una tupla que sólo puede contrastarse contra 12 caracteres.

### ALTA — F-10: P-I7 considera que un `break` cae hasta una retirada inalcanzable y deja sobrevivir un nuevo mutante

**Ubicación:** `../ws/tools/tests/test_instance_fence.py:904-946,974-991`.

El cambio de conjuntos arregla el split-branch, pero los bucles se reducen al resultado lineal de su cuerpo y todo nodo no reconocido —incluido `ast.Break`— se clasifica como `fall`. La secuencia continúa entonces hasta una retirada que en Python es inalcanzable.

[EXACT] Delta literal R2→R3 relevante (`test_instance_fence.py:931-939`):

```diff
+    if isinstance(stmt, (ast.With, ast.AsyncWith)):
+        return _sequence_outcomes(stmt.body, retiring)
+    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
+        return _sequence_outcomes(stmt.body, retiring)
+    if _expr_calls_retire(stmt, retiring):
+        return {_RETIRE}
+    if _expr_persists(stmt):
+        return {_BAD}
+    return {_FALL}
```

[EXACT] Mutante nuevo aplicado sólo a una copia temporal de `process_lifecycle.py`:

```diff
@@ def admin_reconcile(...):
+        if reason == "__mutant_loop_break__":
+            run = self.manifest.get(run_id)
+            run.state = "EXITED"
+            for _item in (0,):
+                break
+                self._retire_run_bindings(run.run_id, "mutant")
+            self.manifest.replace(run)
+            return {"reconciled": True, "run_id": run_id, "state": "EXITED"}
```

[EXACT] Resultado fresco del control y del mutante contra `ExitedBindingInvariantTest.test_every_exited_path_retires_bindings`:

```text
CONTROL: Ran 1 test in 0.354s — OK
MUTANTE: Ran 1 test in 0.344s — OK
```

**Fallo concreto:** en ejecución real, `break` salta `_retire_run_bindings`, luego el run se persiste `EXITED` y retorna. El detector inventa el camino `break → retire`, obtiene `{retire}` y da verde. Por tanto todavía no acredita “todas las salidas” de C3/P-I7.

**Fix sugerido:** modelar `break`, `continue`, `raise` y la salida de cero iteraciones como resultados de control distintos, y resolverlos al salir del bucle; una alternativa más robusta es construir un CFG mínimo por asignación `EXITED`. Este mutante debe quedar como cuarto control permanente.

### Comprobaciones sin hallazgo

- **P-I8:** cerrado en `../ws/tools/tests/test_session_e2e.py:227-233`; después de `ok`, el helper exige por identidad `dispatchable is True`.
- **P-I7 split-branch:** el tercer control permanente fue añadido en `test_instance_fence.py:732-736` y el mutante de ronda 2 ya no sobrevive. F-10 es un camino distinto.
- **Regresión estructural R2→R3:** censo AST sobre todos los `.py` no-backup: 2.488 métodos `test_*` antes y después; cero añadidos, borrados o renombrados. Decoradores `skip`/`expectedFailure`/`xfail`: 7 antes y 7 después, sin delta. El conjunto de ficheros de `tools/tests` tampoco cambió.
- **Atributos privados:** el delta no añade lecturas `coordinator._…` y elimina la lectura cruda de `_active`. Persisten lecturas white-box anteriores en tests como `test_loopback.py:772,1000,1022` y `test_session_coordination.py:851-853`; no pertenecen a este delta ni se usan para resolver el dueño del helper.
- **Superficie:** los cinco SHA-256 suministrados coinciden. `test_client_mode.py` es byte a byte idéntico a R2.
- **Gates solicitados — última línea literal:** `product_frozen.sh` rc 0 → `PRODUCTO-CONGELADO OK (63 ficheros)`; `run_oracle_tests.sh` rc 0, `PASS=10 FAIL=0 UNMET=0` → `ORACULO-TESTS-VERDE`; `suite_full.sh` rc 1 → `SUITE-COMPLETA ROJA`.
- **Rojo ambiental de suite:** ejecutó 2.524 tests y produjo 47 `ERROR` más 1 `FAIL`. Reproducciones dirigidas mostraron `invalid_dayz_test_path_authority`, `native_launcher_create_failed` causado por `PermissionError` al resolver `C:\Users\guill`, y el único `FAIL` (`test_run_daemon_candidate_wave_and_cross_port_publish_one_generation`) terminó sin payload porque un subprocess abortó con `psutil.AccessDenied` y `RunsBackupGateError("process_scan_incomplete")`. Son las familias ambientales anticipadas y no las atribuyo al delta.

## ASERCIONES CAMBIADAS

| Test | Antes (R2) | Después (R3) | C-item |
|---|---|---|---|
| `ExitedBindingInvariantTest.test_every_exited_path_retires_bindings` | Dos controles permanentes: bypass y orden invertido. | Añade el mutante split-branch; el detector pasa de veredicto escalar a conjuntos de salidas. | C3 / P-I7 |
| `SessionE2ETest.adopt_run` | Sólo `result.get("ok") == True`. | Conserva `ok` y añade `result.get("dispatchable") is True`. | C4 / P-I8 |

No cambió ninguna otra aserción de test. `test_daemon.py:95-100` sólo cambia la preparación del fixture para pasar `owner=` explícito; no altera una aserción.

## BACKLOG

- No hay otro defecto concreto reproducido fuera del alcance del lote. Las lecturas privadas white-box citadas arriba son preexistentes y no se elevan por mera presencia.

## NO VERIFICADO

- No obtuve una suite completa verde: el gate terminó exactamente en `SUITE-COMPLETA ROJA` por los fallos ambientales descritos. Los gates de congelación/oráculo y las pruebas dirigidas no sustituyen ese resultado.
- No pude demostrar byte a byte que `gate/allowed_red.txt` no cambió desde R2 porque `../ws-frozen-r2` no contiene `gate/`. La copia actual tiene SHA-256 `5f84e9454383d96cdb307d5217a7e5f6d006a4af6fd63fc426862e4c7c192be2` y exactamente los dos rojos permitidos de freeze; eso no prueba por sí solo ausencia de modificación.
- No pude verificar el estado Git “sin trackear/no se commitea” de `test_session_e2e.py`: el snapshot `../ws` no expone metadatos Git utilizables. Sí verifiqué su SHA-256 entregado y revisé su delta byte a byte.
- No verifiqué parches para F-08/F-09/F-10. Esta ronda es de revisión, el producto permaneció congelado y no se modificó `../ws`.
- No actualicé memoria externa de proyecto: el artefacto durable solicitado para esta revisión no interactiva es este `REVIEW-CODEX.md`, y el vault está fuera de las raíces de escritura de la sesión.
