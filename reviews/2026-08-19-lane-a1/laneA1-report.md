# Lane A1 — Agujero 1 (tombstones)

Agujero 1 cerrado: el dueño de la caja (claim o lease activo) ya no recibe `operation_tombstones_saturated`. Suite final: `Ran 1626 tests in 235.980s` / `OK (skipped=6)`.

---

## 1. Producción tocada

Tres ficheros. Código de error `operation_tombstones_saturated`, tope 128 y TTL 120 s intactos. Agujeros 2 y 3 no se tocaron.

### `C:\Users\guill\AppData\Local\Temp\mcp-laneA1-20260819\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\session_coordination.py`

- `295:299` — `acquire`: el `if len >= MAX` pasa a exigir `not _admission_privileged_locked(client)` y devuelve `_tombstone_saturated_error_locked()`.
- `718:722` — `enqueue`: la misma regla (camino real de `session_acquire_wait`).
- `797:803` — `cancel_operation`: unseen a capacidad también deja pasar si el cliente es privilegiado (puede acuñar el 129.º, igual que el cancel admitido de hoy).
- `2752:2757` — `_admission_privileged_locked`: `_active.client == client` o `_box_claiming == client.session_id`. No mira `_box_queue`.
- `2759:2768` — `_oldest_tombstone_retry_after_locked`: segundos hasta el tombstone más viejo, clamp `[0, OPERATION_TOMBSTONE_TTL_S]`.
- `2770:2781` — `_tombstone_saturated_error_locked`: payload con `error`/`count`/`capacity` más `retry_after_s` y `hint` (`wait Ns then retry session_acquire_wait; do not spin`).

### `C:\Users\guill\AppData\Local\Temp\mcp-laneA1-20260819\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\control_client.py`

- `22:37` — `ControlClientError` acepta `hint: str | None = None`. `str(error)` = `code` o `code: {hint}`. `.code` no cambia.
- `201:208` — `_request_once` pasa `hint=response.get("hint")` solo si es `str`.

### `C:\Users\guill\AppData\Local\Temp\mcp-laneA1-20260819\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py`

- `709:710` — `public_error_code`: si `error.hint`, devuelve `str(error)` (mismo patrón que `LEASE_REQUIRED_RECIPE`).
- `118` — no tocado (`"operation_tombstones_saturated"` sigue en `_REMOTE_ERROR_CODES`).

Tests nuevos: `C:\Users\guill\AppData\Local\Temp\mcp-laneA1-20260819\DayZ Projects\DayZ_MCP_dev\tools\tests\test_session_acquire_wait.py` (`136:195` coordinador, `300:346` ToolError).

Backups previos a editar: `session_coordination.py.bak-agujero1`, `control_client.py.bak-agujero1`, `server.py.bak-agujero1`.

---

## 2. Log rojo (`red_first.log`)

Cuatro tests contra producción sin tocar. `Ran 4 tests in 0.031s` / `FAILED (failures=4)`.

| Test | Mensaje rojo |
|---|---|
| `test_box_claimer_enqueue_is_not_fenced_when_tombstones_saturated` | `AssertionError: 503 == 503` en `assertNotEqual(status, 503)` (`test_session_acquire_wait.py:149`) |
| `test_box_claimer_acquire_with_new_operation_id_is_not_fenced_when_saturated` | `AssertionError: 503 == 503` en `assertNotEqual(status, 503)` (`:171`) |
| `test_saturated_error_tells_stranger_to_wait_then_retry` | `AssertionError: None is not an instance of <class 'str'>` — `payload["hint"]` ausente (`:189`) |
| `test_session_acquire_wait_toolerror_includes_tombstone_recipe` | `AssertionError: 'session_acquire_wait' not found in 'operation_tombstones_saturated'` (`:344`) |

Eso es el rojo que se guarda. El test 4 no nació así.

### Test 4: reescritura hasta fallar por la razón correcta

Primera versión: mock de `request_with_refresh` devolviendo HTTP 503 con el payload de (3), y llamada a `runtime.session_acquire_wait`. Salió rojo, pero con:

```
'operation_tombstones_saturated' not found in 'client_policy_untrusted_open_new_session'
```

`_request_once` (`control_client.py:145-151`) llama `self.policy.revalidate()` **antes** de HTTP. El fixture `AccreditedDaemonPolicy` es frozen y `revalidate` lanza; el 503 nunca se parseaba. Ese rojo no medía el defecto (código desnudo sin receta): medía que la política del fixture no revalida.

Segundo intento: `patch.object(runtime._control.policy, "revalidate")` sobre la instancia. `ERROR` — `dataclasses.FrozenInstanceError: cannot assign to field 'revalidate'`.

Versión que se quedó: mock de `request_with_refresh` (el 503 HTTP real) más `patch.object(type(runtime._control.policy), "revalidate", return_value=None)` para que `_request_once` llegue a `_decode_body` y al `raise ControlClientError`. Contra código sin `hint`, `str(ToolError)` es exactamente `operation_tombstones_saturated`. Ese sí es el defecto.

Por qué el primer fallo no valía: un arreglo de receta en `_request_once`/`public_error_code` lo habría dejado rojo igual (seguiría muriendo en `revalidate`). Un test que no recorre el `raise` de `_request_once` no cierra el modo de fallo (c) del spec.

---

## 3. Log verde

Números del orquestador, tomados de `green.log`:

```
Ran 1626 tests in 235.980s
OK (skipped=6)
```

Base efectiva previa: 1622 verdes. Cuatro tests nuevos: 1626. Ningún rojo añadido, ningún test perdido.

Las tres anclas siguieron verdes al correrlas solas tras el parche:

- `test_tombstone_capacity_fences_unseen_operations_and_recovers_after_ttl`
- `test_tombstone_saturation_does_not_block_cleanup_of_admitted_operation`
- `test_timeout_tombstones_exact_operation_and_clears_state`

---

## 4. Tres modos de fallo (spec §6)

**a) Privilegio demasiado ancho (`_box_queue` en vez de `_box_claiming`).**

- Código: `_admission_privileged_locked` `session_coordination.py:2755` — solo `_box_claiming == client.session_id`, no la cola.
- Test: `test_box_claimer_enqueue_is_not_fenced_when_tombstones_saturated` (`:145-155`). W entra a la cola de caja con `box_wait_touch(..., claim=False)`; L tiene el claim. L hace enqueue 202; W recibe 503. Si el privilegio fuera “está en `_box_queue`”, W pasaría.

**b) Privilegio en `acquire` y olvidado en `enqueue` (o al revés).**

- Código: `acquire` `295:299` y `enqueue` `718:722` usan el mismo helper.
- Tests: `test_box_claimer_enqueue_is_not_fenced_when_tombstones_saturated` cubre `enqueue` (el camino de `session_acquire_wait`). `test_box_claimer_acquire_with_new_operation_id_is_not_fenced_when_saturated` cubre `acquire` con `operation_id` nuevo y L sin lease. Los dos tenían que pasar; uno solo no basta.

**c) Receta solo en el JSON HTTP, no en lo que ve el modelo.**

- Código: `ControlClientError` `22:37`, `_request_once` `201:208`, `public_error_code` `709:710`.
- Tests: `test_saturated_error_tells_stranger_to_wait_then_retry` exige `hint` y `retry_after_s` en el body HTTP. `test_session_acquire_wait_toolerror_includes_tombstone_recipe` exige que `str(ToolError)` lleve `session_acquire_wait` y `do not spin`, no el código desnudo.

---

## 5. Cómo llega cada test al assert

Ninguno inyecta `_operation_tombstones` a mano ni salta `cancel_operation` para acuñar.

**Tests 1–3** (coordinador real):

1. 128 tombstones: bucle `cancel_operation(_identity("a"), f"operation-{index}")`, `status == 200`. Un extraño, 128 `operation_id` distintos. Misma forma que `test_tombstone_capacity_fences_unseen_operations_and_recovers_after_ttl`. La clave es `(ClientIdentity, operation_id)`; no hace falta 128 identidades.
2. Claim de caja: `box_wait_touch(owner, claim=True)` y `assertTrue(claimed["box_claimed"])`. Tests 1 y 2.
3. Luego `enqueue` / `acquire` / el body del extraño.

Atajos que no son de saturación: ninguno en 1–3. El waiter W del test 1 es extra (spec no lo pedía) para cerrar el modo (a).

**Test 4** (capa cliente, no coordinador):

No acuña 128 tombstones. El spec pide mock HTTP 503 con el payload de (3). El 503 lo fabrica el test en `request_with_refresh` (`:322-323`). El camino de producción que sí se recorre: `_request_once` → `_decode_body` → `ControlClientError(..., hint=)` → `ClientRuntime.session_acquire_wait` → `_control_with_lazy_spawn` → `public_error_code` → `ToolError`.

Atajos declarados:

- `patch` de `revalidate` en la **clase** de la policy, para que el fixture llegue al parseo HTTP. Sin eso el test no mide el defecto.
- `/session/cancel-operation` del mock devuelve 200 para que el `finally` de `session_acquire_wait` no se quede en el bucle de cleanup. El assert mira el `ToolError` primario del enqueue, no el cancel.

No se sustituyó `_request_once` por un `ControlClientError` construido a mano (eso habría saltado la extracción de `hint`).

---

## 6. Spec vs código real

- **Ya conocido.** Spec §B: fencing no toca `server.py`. Este árbol sí tiene el bloque `86:223` (`_REMOTE_ERROR_CODES`, `_DAYZ_TEST_VALUE_ERROR_CODES`, `compute_bridge_ready`). No afecta al agujero 1. El único cambio de este trabajo en `server.py` es `public_error_code` `709:710`.
- **Citas del spec desplazadas tras el parche** (eran correctas en el árbol sin tocar): acquire saturado spec `:295-300` → ahora `295:299` con privilegio; enqueue spec `:719-724` → `718:722`; cancel spec `:799-806` → `797:803`; `_request_once` spec `:197-202` → `201:208`; `ControlClientError` spec `:22-34` → `22:37`.
- **Helper no listado en el Forward Contract.** `_tombstone_saturated_error_locked` llama `_oldest_tombstone_retry_after_locked` (`2759:2768`). El spec lo usa en el DESIGN y no lo nombra en la tabla. No es un desvío: es la función que el DESIGN invoca.
- **§B acierta en `session_coordination.py`.** Grep `instance_fence` / `_bound_queues` / `_retire_run_bindings`: vacío. El agujero 1 aterriza aquí sin chocar con fencing.
- **Escenario vs tests ancla.** El spec §2 habla de 128 clientes distintos. Los tests ancla y los de este arreglo usan un extraño × 128 `operation_id`. El presupuesto es el dict, no el número de PIDs.

Nada del spec del agujero 1 me pareció equivocado en la regla. Se siguió el DESIGN (helpers, privilegio, receta). No se improvisó LRU ni se subió el tope.

---

## 7. Qué no verifiqué

- Runtime DayZ / puente / MCP in-game: prohibido en el encargo.
- Que consumidores comparen `str(error)` en vez de `.code` y se rompan al añadir `: {hint}`. El DESIGN deja `.code` igual; no ejecuté un grep de consumidores fuera de la suite.
- Hashes del árbol OneDrive (la frontera era no escribir ahí; no re-fotografié).
- `_oldest_tombstone_retry_after_locked` con reloj que no sea `FakeClock` en 0 (el test 3 solo exige `float` en `[0, 120]`; con este reloj sale `120.0`).
- Privilegio por lease activo (`_active.client == client`) con un test propio. El helper lo implementa (`2753:2754`); los cuatro tests del spec cubren claim de caja, no al holder del lease. La ancla de cancel admitido sigue cubriendo cleanup del lease.
- La suite de 1626 la cerró el orquestador. Yo corrí en rojo los 4, en verde los 4 + 3 anclas, y un subconjunto de riesgo (292 tests) antes de la suite entera. Los números finales de `green.log` los tomo del orquestador, no los reconté aquí.
