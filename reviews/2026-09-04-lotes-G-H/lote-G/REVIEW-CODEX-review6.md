## VEREDICTO

NO es seguro integrarlo: hay 2 incumplimientos reproducibles —uno ALTO de autoridad tras `release` y uno MEDIO de P5—, ambos de familias ya visitadas.

## HALLAZGOS

### ALTA — H1: el `release` normal deja despachable el binding de un run ya `RUNNING_IDLE`

**Ubicación:** `../ws/tools/dayz_mcp/process_lifecycle.py:765-780`, `../ws/tools/dayz_mcp/process_lifecycle.py:2135-2166`, `../ws/tools/dayz_mcp/loopback.py:1081-1101`, `../ws/tools/dayz_mcp/loopback.py:1120-1125`.

[EXACT] La transición durable sale de `RUNNING`, pero ninguna de las dos entradas de release retira bindings:

```python
# ../ws/tools/dayz_mcp/process_lifecycle.py:765-780
def release_owner(self, session_id: str, lease_id: str) -> list[str]:
    ...
    if current.state == "RUNNING":
        ...
        run.state = "RUNNING_IDLE"

# ../ws/tools/dayz_mcp/process_lifecycle.py:2135-2140
def release_owner(self, session_id: str, lease_id: str) -> list[str]:
    with self._operation_lock:
        ...
        changed = self.manifest.release_owner(session_id, lease_id)

# ../ws/tools/dayz_mcp/process_lifecycle.py:2159-2166
if not unacknowledged:
    released = self.manifest.release_owner(session_id, lease_id)
    ...
    result.update({
        "terminal_safe": True,
        "runs_released": released,
    })
```

[EXACT] El cerco de enqueue tampoco considera `RUNNING_IDLE` como salida de RUNNING, por lo que devuelve la cola del binding:

```python
# ../ws/tools/dayz_mcp/loopback.py:1097-1101
return getattr(run, "state", None) in {
    "EXITED",
    "STOPPING",
    "UNRECONCILED",
}

# ../ws/tools/dayz_mcp/loopback.py:1120-1125
if mutation:
    if len(bound) == 1:
        if self._bound_run_has_left_running(_binding_run_id(bound[0])):
            return "binding_retired", None, None
        instance = bound[0].instance
        return None, self._bound_queues.setdefault(instance, []), instance
```

**Reproducción determinista:** instalé un binding BOUND de servidor para un run RUNNING reconocido, ejecuté `begin_release_owner`, esperé su evento terminal y encolé/polleé `world_spawn`.

```text
terminal_result = {"runs_released":["run-existing"],"terminal_safe":true}
manifest         = {"state":"RUNNING_IDLE","owner":null}
enqueue          = [200,{"cmd":"world_spawn","id":1,"peer":"server"}]
poll             = [200,{"bind":"BOUND","commands":[{"cmd":"world_spawn",
                    "args":{"classname":"SurvivorM_Mirek"},"id":1}]}]
```

**Fallo concreto:** después de que release declara cierre seguro y elimina el dueño, el daemon acepta y entrega una mutación del mundo al proceso huérfano, sin una adopción nueva. No es un solo un diagnóstico inexacto: viola autoridad y P4. No provoca crash.

**Fix sugerido:** [DESIGN] en ambas entradas (`release_owner` directo y la rama reconocida de `begin_release_owner`), identificar bajo `_operation_lock` los runs RUNNING que van a soltarse y retirar sus bindings **antes** de `manifest.release_owner`; si la persistencia falla, el manifiesto puede quedar RUNNING pero el binding ya no será despachable, que es el lado seguro. Añadir además `RUNNING_IDLE` al rechazo de `_bound_run_has_left_running` como defensa fail-closed. Fixture mínimo: `release reconocido -> world_spawn rechazado`; la ruta de adopción deberá establecer explícitamente qué binding nuevo/confirmado habilita el despacho posterior.

### MEDIA — H2: P5 tiene una ventana posterior a la compensación que vuelve a acreditar el run restaurado

**Ubicación:** `../ws/tools/dayz_mcp/process_lifecycle.py:1076-1082`, `../ws/tools/dayz_mcp/process_lifecycle.py:1308-1327`, `../ws/tools/dayz_mcp/process_lifecycle.py:1713-1735`, `../ws/tools/dayz_mcp/loopback.py:1097-1101`, `../ws/tools/dayz_mcp/loopback.py:1607-1611`.

[EXACT] La compensación borra primero y el rollback durable ocurre después de cerrar el handle, operación que puede esperar cinco segundos:

```python
# ../ws/tools/dayz_mcp/process_lifecycle.py:1076-1082
def _forget_attempt_activity(self, run_id: str, attempt_started_at: float) -> None:
    key = self._activity_key(run_id)
    with self._activity_lock:
        current = self._last_activity.get(key)
        if current is not None and current >= attempt_started_at:
            self._last_activity.pop(key, None)
        self._bump_box_revision_locked()

# ../ws/tools/dayz_mcp/process_lifecycle.py:1308-1327
if attempt_started_at is not None:
    self._forget_attempt_activity(provisional.run_id, attempt_started_at)
...
confirmed_closed = launched is None or self._terminate_open_handle(launched)
...
self.manifest.replace(target)

# ../ws/tools/dayz_mcp/process_lifecycle.py:1725-1727
try:
    launched.wait(timeout=5.0)
    return True
```

[EXACT] Durante esa ventana el manifiesto sigue STARTING; el cerco no lo rechaza y un enqueue no interno acredita actividad después de publicar la orden:

```python
# ../ws/tools/dayz_mcp/loopback.py:1097-1101
return getattr(run, "state", None) in {
    "EXITED",
    "STOPPING",
    "UNRECONCILED",
}

# ../ws/tools/dayz_mcp/loopback.py:1607-1611
try:
    if not internal:
        self._note_run_command_activity(
            owner_client, run_id=commanded_run_id
        )
```

**Reproducción determinista:** run heredado con servidor BOUND; se intenta añadir cliente; el `replace` final RUNNING falla; justo después de `_forget_attempt_activity` se encola un `query_all_players` legítimo al servidor heredado, antes de restaurar el manifiesto.

```text
manifest_at_enqueue = STARTING
enqueue             = [200,{"cmd":"query_all_players","id":1,"peer":"server"}]
start_error         = lifecycle_start_failed
restored            = {"state":"RUNNING","processes":1}
box                 = {"activity_state":"recent","last_activity_age_s":0.008}
```

**Fallo concreto:** un intento que no llega a durable restaura el run original con `recent`, no con `unknown`; incumple P5. La actividad sigue siendo diagnóstica, así que la severidad es degradación y no autoridad, corrupción ni crash.

**Qué olvida exactamente:** solo `_last_activity[(daemon_generation, run_id)]` cuando su sello es `>= attempt_started_at`; no toca el sticky `_activity_unknown`. Por tanto, un sello legítimo estrictamente anterior al intento se conserva. Un comando legítimo del run heredado solapado antes de la compensación sí se borra por diseño; uno que cae después de la compensación —la reproducción— sobrevive indebidamente.

**Fix sugerido:** [DESIGN] hacer que los créditos producidos mientras el intento está activo queden asociados a la época/id del intento y descartar esa época al resolver rollback, antes de reabrir el run restaurado a créditos normales. Un segundo `pop` sin esa separación no es suficiente: puede borrar el primer comando legítimo posterior al rollback.

## FAMILIAS

**Respuesta de proceso:** no apareció una familia nueva. H1 pertenece a **identidad/autoridad del destino** (se omitió un sibling del release reconocido); H2 pertenece a **frontera de generación/linearización de actividad** (la compensación no cubre toda la ventana del intento). El rediseño cerró casos de esas familias, pero no sus dos intercalaciones restantes.

### Foto P1

P1 pasa en la derivación de la respuesta. `box_occupancy` toma `_BoxSnapshot` una vez en `process_lifecycle.py:2651-2666`; el manifiesto se lee en `:2669`, luego `_last_activity`, `_activity_unknown` y `_box_revision` se copian bajo `_activity_lock` en `:2670-2686`, y `_derive_box` consume solo `snapshot` y `probes` en `:243-272`. La E/S de argv y la sonda diag está fuera del lock en `:2695-2751`. La ruta de lectura no toma `_operation_lock`.

Sí existe una lectura viva posterior de `_box_revision` en `process_lifecycle.py:2768-2770`, además de la lectura de `_box_cache` en `:2757-2758`. La primera decide únicamente si el resultado de sondas se puede guardar para llamadas futuras; no cambia `snapshot`, `probes` ni la respuesta corriente. No hay relectura posterior de manifiesto, `_last_activity` o `_activity_unknown`, ni E/S bajo `_activity_lock`; `time.monotonic()` no es E/S. No encontré inversión `_activity_lock -> _operation_lock` en esta ruta.

### Caché P2

P2 pasa. Las filas se reconstruyen desde el nuevo snapshot en cada llamada; el caché contiene solo `_BoxProbes`. El TTL es exactamente 1,5 s (`process_lifecycle.py:38`, `:2760-2765`). Un cache hit conocido y vacío puede ocultar un foreign nuevo durante ese intervalo; uno viejo puede producir un falso ocupado durante el mismo intervalo. Un scan desconocido publica `occupied=True` (`:264`) y por tanto falla cerrado. `start_run` vuelve a sondear mediante `_foreign_diag_reason` (`:2838-2846`). No encontré vía que prolongue la sonda vieja sin límite ni que convierta `scan_known=False` en caja libre.

### High-water mark P3

P3 pasa. La observación sin binding captura `epoch` antes de leer el manifiesto y solo elimina `current <= epoch` (`process_lifecycle.py:1012-1034`); por tanto no borra un sello acreditado posterior. `_write_command_activity` conserva el máximo de épocas y no escribe si el key está en `_activity_unknown` (`:1036-1053`). La observación sin binding no limpia ese sticky. Si es posterior al último sello, elimina el sello y la foto siguiente publica `unknown`.

### Seis caminos P4 y fallo intermedio

| Camino | Retirada | Primera transición durable posterior | Estado si falla esa persistencia; seguridad |
|---|---:|---:|---|
| stop | `process_lifecycle.py:1930` | RUNNING→STOPPING, `:1931-1933`; luego STOPPING→EXITED, `:2029-2034` | Si falla la primera: durable RUNNING, binding ya retirado. Si falla la final: durable STOPPING, binding retirado. No hay despacho. La rama de identidad incierta también retira en `:1862` antes de UNRECONCILED en `:1864`. |
| release no reconocido | `:2207`, `:2235` o `:2254` | UNRECONCILED/EXITED en `:2209`, `:2237` o `:2255` | Si falla: queda el estado durable previo, incluido RUNNING, pero el binding fue retirado; no hay despacho. |
| release reconocido | **No existe** (`:2135-2140`, `:2159-2166`) | RUNNING→RUNNING_IDLE dentro de `RunManifestStore.release_owner`, `:770-780` | **Inseguro:** el binding sigue BOUND y despachable; es H1. |
| repair de run | `:2356` | →EXITED en `:2358` | Si falla: persiste el estado anterior (normalmente UNRECONCILED), binding retirado; no hay despacho. |
| repair de manifiesto | `:2444` | →EXITED en `:2446` | Si falla: el store restaurado conserva su registro anterior, binding retirado; no hay despacho. |
| reap | `:2517` | →EXITED en `:2519` | Si falla: persiste el estado reapable anterior, binding retirado; no hay despacho. |
| reconcile | `:2949` cuando no hay supervivientes | →EXITED en `:2951` | Si falla: persiste el estado reconciliable anterior, binding retirado; no hay despacho. Con supervivientes, `:2947-2951` publica RUNNING_IDLE sin retirar: sus entradas UNRECONCILED/STARTING/STOPPING ya deberían llegar cercadas por la transición previa, pero una entrada RUNNING_IDLE creada por el release defectuoso de H1 conserva el binding y reconcile tampoco lo corrige. Es la misma familia y el mismo binding, no un tercer hallazgo independiente. |

La retirada real se ejecuta bajo el lock de loopback: elimina los índices por rol (`loopback.py:1006-1012`), descarta la cola (`:1045-1053`) y elimina binding y cola (`:1062-1063`). Por eso los fallos intermedios listados son fail-closed salvo el release reconocido, donde nunca se invoca.

### Compensación P5

P5 falla por H2. La comparación local del sello es correcta y no borra actividad anterior al intento, pero la compensación no delimita toda la vida del intento: ocurre antes de cerrar el proceso y antes del rollback durable.

### Validaciones preexistentes

No encontré debilitamiento de `assert`, `raise` ni rechazos públicos en el contraste textual contra `../integ`. Las únicas guardas de tipo eliminadas pertenecían a `_with_live_activity`, que procesaba un `dict` genérico; esa función completa desaparece al sustituirse por `_BoxSnapshot` tipado y derivación interna. Sí se añadieron rechazos de binding. Esta conclusión queda limitada por la procedencia no autenticada de `../integ`, declarada abajo.

Verificación ejecutada con el intérprete canónico `tools/.venv-mcp`: `py_compile` de ambos archivos terminó con exit 0; las suites enfocadas `test_box_occupancy test_process_lifecycle test_lifecycle_http test_loopback test_mcp_tools test_session_coordination` terminaron `Ran 332 tests ... OK`. Ninguna de esas 332 pruebas discrimina las dos reproducciones anteriores.

## BACKLOG

- El gate AST `../ws/tools/tests/test_instance_fence.py:647-699` solo busca asignaciones literales a EXITED y la presencia de alguna llamada `_retire_run_bindings` en la misma función. No ve RUNNING→RUNNING_IDLE, no prueba orden ni distingue ramas; por construcción no puede detectar H1. Es problema del gate, fuera del producto revisado.
- Falta un fixture que inyecte un crédito de un sibling heredado **después** de `_forget_attempt_activity` y antes del rollback durable. El fixture actual cubre solo el crédito anterior a la compensación.
- No revisé ni propongo cambios en el sobre de `dayz_test_stop`, `runtime_state.py`, `README-mcp.md`, gate, oráculo, ledger o brief.

## NO VERIFICADO

- No forcé una intercalación real en la ventana sin E/S entre `manifest.list_runs()` y la toma de `_activity_lock`; queda el residuo declarado de P1.
- No ejecuté la suite global completa del proyecto ni DayZDiag/in-game; ejecuté 332 pruebas enfocadas y dos reproducciones deterministas con seams de test.
- `../ws` no contiene metadatos Git. Verifiqué los SHA-256 pedidos (`2195ca23...ea04` y `52ee08f8...e1cb`), pero no pude autenticar que `../integ` sea el padre inmediato ni reconstruir un diff de commit oficial; por eso el dictamen sobre “validaciones debilitadas” es un contraste de snapshots, no de procedencia.
- No provoqué fallos reales de disco/FS ni esperas reales de cinco segundos; los estados intermedios se verificaron por lectura de los caminos y las carreras mediante sustituciones deterministas.
- Todo archivo fuera de los dos hashes indicados quedó fuera del dictamen de producto; solo consulté tests/contexto para construir discriminadores y BACKLOG.
