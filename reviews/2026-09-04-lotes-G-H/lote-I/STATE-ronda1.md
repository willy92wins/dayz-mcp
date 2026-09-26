## HECHO

Producto intacto (`tools/dayz_mcp/` no se tocó). `test_client_mode.py`, `test_daemon.py` y `test_session_e2e.py` no se editaron: el fixture A en `bind_both_peers` cubre los 14+5+1 rojos de esos módulos. `tests/test_session_e2e.py` sigue sin trackear y no se commiteará (R5).

### `tools/tests/fence_helpers.py`
- `bind_both_peers` (`:15`) llama a `_register_manifest_run` (`:31`) después de instalar los dos peers.
- `_register_manifest_run` (`:35`): si `state.lifecycle.manifest` tiene `add` (ProcessLifecycle real), registra `RunRecord(run_id, fence-fixture-owner, fence-fixture-lease, RUNNING, …)` con `ProcessRecord` server/client (pids `PID_SERVER`/`PID_CLIENT`, scheme `psutil-argv-v2`) antes de la primera orden. Si el run ya está, no hace nada. Si el lifecycle es `_BoundPeerDispatchable` (sin `add`), no registra — C5.

### `tools/tests/test_instance_fence.py`
- Helper `_ensure_dispatchable_lifecycle` (`:121`): lifecycle None → `_BoundPeerDispatchable(run_id)`; si ya hay `manifest.get`, no pisa; si hay `guard` sin `manifest.get` → `SimpleNamespace(guard=…, manifest=_BoundPeerDispatchable(run_id))`.
- `LifecycleFenceTest.setUp` (`:422`): `self.state.lifecycle = self.lifecycle` (las dos direcciones, como el daemon). Aserciones de `admin_reconcile` / `client_relaunch` / `repair_manifest_recovery` / `repair_recovery_fault` byte a byte iguales.
- `ProductionAttributionFenceTest.setUp` (`:1003`): `_ensure_dispatchable_lifecycle(self.state, "run-attr")`.
- `Round4FenceRegressionTest._enqueue_client_mutation` (`:1172`): mismo helper antes del enqueue (cubre los 3 tests de creation_time que asertan 200).
- `test_lookup_creation_time_via_lifecycle_like_daemon` (`:1184`) y `test_confirm_and_lookup_share_creation_time_format` (`:1251`): el `SimpleNamespace(guard=guard)` ahora lleva `manifest=_BoundPeerDispatchable(…)`.
- `test_accredited_mutation_does_not_increment_unaccredited_delivery` (`:1422`): helper antes del enqueue.
- `Round5FenceRegressionTest.test_unread_creation_time_has_own_class_not_ambiguous` (`:1549`): helper antes del enqueue.
- `ExitedBindingInvariantTest.test_every_exited_path_retires_bindings` (`:708`): el visitante acepta una llamada a una función cuyo cuerpo (mismo módulo) llama a `_retire_run_bindings`; y comprueba orden en `_commit_retirement`: `_retire_run_bindings` precede a `manifest.replace` (`:792`). Ampliado, no relajado.

### Aserciones reescritas (R2)

| test | aserción vieja | aserción nueva | C-item |
| --- | --- | --- | --- |
| `LifecycleFenceTest.test_release_owner_retires_bindings` | `_mark_unacknowledged` + `_assert_binding_retired(instance)` (binding ausente de `_bindings`, en `_retired_instances`, cola vacía) | binding sigue `BOUND`; mutación `run_not_owned` 409; poll acreditado `commands: []`; `adopt_run` → `{ok, run_id, state, dispatchable=True}`; mutación siguiente 200 | C2 (+ C4 en la respuesta de adopt) |

## GATE

```
PRODUCTO-CONGELADO OK (63 ficheros)
```

```
Ran 2524 tests in 196.618s
rojos = permitidos (2)
SUITE-COMPLETA OK
```

## CONTROL POSITIVO

### C2 — `test_release_owner_retires_bindings`

Replay de la aserción vieja (`_assert_binding_retired`) sobre el camino C2 (release de dueño reconocido, sin `_mark_unacknowledged`):

```
AssertionError: '3540e924-7bd9-417e-9d11-59aec389625b' unexpectedly found in {'3540e924-7bd9-417e-9d11-59aec389625b': Binding(instance='3540e924-7bd9-417e-9d11-59aec389625b', run_id='run-fence-1', role='client', epoch=1, pid=9001, creation_time_utc='2026-08-18T00:00:01.000000Z', state='BOUND', presented_pids=set(), last_present_at={}, last_poll_at=None, last_attributed_pid=None, last_verified_at=None)}
```

C2 supersede: el release cerca el run (`_fenced_runs`) y persiste `RUNNING_IDLE`; no llama `_retire_run_bindings`. El binding permanece `BOUND`; la mutación siguiente es `run_not_owned`; `adopt_run` rehabilita el mismo binding.

### D — mutante de orden en `_commit_retirement`

Copia temporal en `%TEMP%/lote-i-mutant/process_lifecycle.py` (`C:\Users\guill\AppData\Local\Temp\lote-i-mutant\`, equivalente Git-bash `/tmp`; el workspace no se tocó). Se invirtió el orden: `manifest.replace` (línea 1078) antes de `_retire_run_bindings` (línea 1081). El mismo criterio del visitante (`retire_line < replace_line`) falló:

```
MUTANT_FAIL: _commit_retirement must retire bindings before manifest.replace
```

Sobre el producto real (replace en 1079, retire en 1077) el test está verde.

## LO QUE NO PUDE VERIFICAR

- Los 29 FAIL originales de fixture (`binding_retired` 409 / `run_state_unavailable` 503) no se recapturaron traza a traza antes de cambiar el fixture; el orquestador ya los había medido. El control positivo de aserción se limitó a C2 y al mutante D.
- El mutante D se evaluó por AST sobre la copia en `/tmp`, no importando ese fichero como `dayz_mcp.process_lifecycle` (eso exigiría sustituir el producto congelado).
- No se lanzó DayZDiag ni un daemon de producción: el gate cubre unittest, no el puente in-game.
- No hay `git` en este workspace; R5 se declara, no se comprueba con `git status`.

## DISPUTAS

Ninguna.
