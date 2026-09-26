## VEREDICTO

NO es seguro integrarlo: queda una carrera de despacho sin dueño en `repair_manifest_recovery`, P7' todavía expone actividad del intento fallido durante el rollback, el cerco se acumula al retirar ciertos runs y `exec_enforce` incumple el error contractual en una intercalación de release.

## HALLAZGOS

### ALTA — `repair_manifest_recovery` vuelve a producir `RUNNING_IDLE` sin cercar y un poll entrega después de la transición

`../ws/tools/dayz_mcp/process_lifecycle.py:2505`, `:2514-2516`, `:2567-2570`:

```python
[EXACT]
        with self._operation_lock:
            ...
            self.manifest = restored
            self._invalidate_box_cache()
            for run in restored.list_runs():
                ...
            try:
                restored.recover_after_restart()
            except Exception:
                return self._manifest_repair_failure("manifest_drift")
```

`../ws/tools/dayz_mcp/process_lifecycle.py:824-843`:

```python
[EXACT]
            for run_id, current in list(self._runs.items()):
                run = self._clone(current)
                if run.state == "RUNNING" and run.owner_session_id is not None:
                    run.owner_session_id = None
                    run.owner_lease_id = None
                    run.state = "RUNNING_IDLE"
                    released.append(run_id)
                ...
                self._runs[run_id] = run
            if released or unreconciled:
                try:
                    self._persist_locked()
                except Exception:
                    self._runs = previous
                    raise
```

No hay `_fence_runs(...)` antes de esa mutación. El poll consulta estado/cerco al tomar su snapshot (`../ws/tools/dayz_mcp/loopback.py:2096-2104`), suelta el lock para `expire_due`/probe (`:2105-2115`) y, al retomarlo, sólo revalida que la cola conserve identidad y prefijo (`:2118-2128`); no vuelve a consultar `_poll_should_hold_commands` antes de añadir al wire en `:2184-2188`.

Fallo concreto reproducido con un `world_spawn` ya encolado: se pausó el poll después de copiar la cola, se ejecutó `repair_manifest_recovery` sobre un `RUNNING` estable y se reanudó el poll.

```text
state_before_poll_commit='RUNNING_IDLE'
fenced=[]
poll=(200, {'commands': [{'id': 1, 'cmd': 'world_spawn', ...}], 'bind': 'BOUND'})
repair={'terminal_safe': True, ...}
```

El comando se despacha cuando el run ya no tiene dueño. Es corrupción de autoridad/destino, no una tolerancia ni un problema cosmético.

El censo completo de productores confirma que la invariante no está centralizada:

- `RunManifestStore.release_owner` (`process_lifecycle.py:768-791`) tiene como único caller productivo a `_quiesce_then_release_owner` (`:1407`): correcto.
- `RunManifestStore.recover_after_restart` (`:816-848`) se invoca directamente desde repair (`:2568`): bypass productivo y reproducido.
- `admin_reconcile` escribe `RUNNING_IDLE` directamente (`:3063-3066`), aunque su duplicación local sí cerca antes de `replace` y revierte en fallo (`:3067-3076`): no reproduce el fallo, pero tampoco pasa por el supuesto método único.
- `RunManifestStore.release_all_running_owners` (`:793-814`) es otro productor público directo. `rg` no encontró caller productivo en `../ws/tools`; por eso no lo elevo como segundo fallo runtime, pero contradice la garantía estructural de productor único.

Fix sugerido: hacer que el método único acepte el conjunto exacto de runs y la transición persistente, y usarlo también alrededor de `recover_after_restart` y `admin_reconcile`; la secuencia debe ser `fence+drain` bajo `ServerState._lock` → transición persistente → confirmar, o `unfence` en cualquier fallo.

### MEDIA — el rollback durable puede publicarse con un sello `recent` del intento fallido

`_compensating` impide nuevas escrituras, pero no oculta un sello del intento que ya existía al entrar. El orden actual deja el dato durable visible antes de borrar ese sello.

`../ws/tools/dayz_mcp/process_lifecycle.py:1347-1357`:

```python
[EXACT]
        try:
            self.manifest.replace(target)
        except Exception:
            return True
        self._invalidate_box_cache()
        if attempt_started_at is not None:
            self._forget_attempt_activity(
                provisional.run_id,
                attempt_started_at,
                rolled_back_at=time.time(),
            )
```

`../ws/tools/dayz_mcp/process_lifecycle.py:1082-1097` no consulta `_compensating_runs` al leer:

```python
[EXACT]
    def _activity_for_run_id(
        self, run_id: str, clock: float
    ) -> tuple[str, float | None]:
        key = self._activity_key(run_id)
        with self._activity_lock:
            if key in self._activity_unknown:
                return "unknown", None
            stamp = self._last_activity.get(key)
        if stamp is None:
            return "unknown", None
        ...
        if age <= _ACTIVITY_STALE_S:
            return "recent", round(age, 3)
```

Sonda: se acreditó actividad después de comenzar el intento y antes de entrar en `_settle_failed_launch`; dentro del `replace` de rollback, justo después de persistir el `RUNNING` previo y mientras el run seguía en `_compensating_runs`, otro lector llamó a `box_occupancy`.

```text
compensating=True
during={'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.008}
after={'state': 'RUNNING', 'activity_state': 'unknown', 'last_activity_age_s': None}
```

Por tanto la tumba sólo corrige la fila después; no es la frontera observable del rollback. La actividad no es autoridad, así que la severidad concreta es degradación/coherencia, no corrupción persistente.

Fix sugerido: durante `_compensating`, hacer que `_activity_for_run_id` publique `unknown` (o establecer una frontera equivalente antes de exponer el rollback, con rollback simétrico si falla la persistencia). Añadir un test que lea después del `replace` durable pero antes de `_forget_attempt_activity`; el test actual sólo comprueba que un escritor concurrente no aterrice.

### MEDIA — retirar físicamente un run no elimina su entrada de `_fenced_runs`

`../ws/tools/dayz_mcp/loopback.py:1015-1021` retira instancias, pero no el cerco del run:

```python
[EXACT]
    def retire_run(self, run_id: str, reason: str) -> None:
        with self._lock:
            keys = [key for key in self._role_index if key[0] == run_id]
            for key in keys:
                instance = self._role_index.pop(key, None)
                if instance is not None:
                    self._retire_instance_locked(instance, reason)
```

La única baja del set está en `unfence_runs` (`../ws/tools/dayz_mcp/loopback.py:1144-1148`). El reaper pone `EXITED` y llama a la retirada física en `../ws/tools/dayz_mcp/process_lifecycle.py:2629-2639`, sin `unfence`.

Fallo concreto reproducido:

```text
before=['run-existing']
reaped=['run-existing']
durable='EXITED'
bindings=[]
fenced_after=['run-existing']
```

El stop normal tras la adopción limpia indirectamente porque `adopt_run` levanta el cerco; los retiros sin adopción —reap, reconcile sin supervivientes y repairs terminales— dejan una cadena por run hasta reiniciar el daemon. Es acumulación de estado lógico y memoria; además una restauración futura del mismo `run_id` heredaría un cerco ajeno a su ciclo de vida.

Fix sugerido: descartar `run_id` de `_fenced_runs` dentro del mismo `ServerState._lock` de `retire_run`, incluso cuando ya no queden entradas en `_role_index`, y cubrir reap/reconcile/repair con una sonda de alta→retiro→ausencia.

### BAJA — la segunda validación de `exec_enforce` pierde `run_not_owned`

`../ws/tools/dayz_mcp/loopback.py:1790-1802`, `:1829-1839`:

```python
[EXACT]
        with self._lock:
            fence_error_code, queue, fence_instance = self._enqueue_fence_target(
                peer, "exec_enforce", internal=internal
            )
            ...
            if fence_error_code is not None or queue is None:
                fence_lost = True
                ...
        ...
        if fence_lost:
            return 409, {"error": "enqueue_cancelled"}
```

Sonda: la primera validación pasó con `RUNNING`; el audit `allowed` se pausó; `release_owner` cercó y persistió `RUNNING_IDLE`; la segunda validación detectó el cerco.

```text
durable='RUNNING_IDLE'
fenced=['run-existing']
result=(409, {'error': 'enqueue_cancelled'})
audit=[('allowed', 1), ('discarded', 1)]
```

No hay despacho ni crédito —el fail-closed funciona—, pero P6 exige `run_not_owned` para todo comando dirigido al run sin dueño. Fix sugerido: conservar el `fence_error_code` de la segunda validación y devolver `_fence_reject_response(fence_error_code)`; reservar `enqueue_cancelled` para stop/generation cancellation.

### Comprobaciones sin hallazgo

- No encontré inversión `ServerState._lock → ProcessLifecycle._operation_lock/_activity_lock`. Bajo el lock del loopback, el estado durable llega a `RunManifestStore.get`; las escrituras de actividad de enqueue se hacen después de soltarlo. En sentido contrario, lifecycle toma `_operation_lock` y luego entra brevemente en el lock del loopback para fence/retire/unfence. No hay camino directo de vuelta.
- `_seal_activity_locked` es el único escritor de `_last_activity` (`process_lifecycle.py:1051-1062`) y todos sus callers pasan por él (`:1003`, `:1078`). El `finally` de `_compensating` elimina la marca tanto si `replace` funciona como si lanza (`:1120-1129`); la sonda existente de fallo de replace confirma que un crédito posterior vuelve a aterrizar.
- Sticky unknown conserva precedencia (`:1075-1078`, `:1086-1091`). La observación sin binding durante compensación sólo elimina sellos/eleva tumba y queda fail-closed; no encontré un escritor alternativo que esquive el cerco.
- `adopt_run` persiste primero el `RUNNING` del `run_id` solicitado y después llama exactamente a `_unfence_runs([run_id])` (`process_lifecycle.py:2239-2249`); no levanta otro run.
- P8 mantiene el orden sello→dato: `_take_box_snapshot` lee `_box_revision` en `process_lifecycle.py:2785-2789` antes de `manifest.list_runs()` en `:2789`; la publicación de una sonda calculada se condiciona a que la revisión siga igual en `:2885-2889`.
- No observé validaciones debilitadas respecto a `../ws-frozen-r8`: las adaptaciones de `test_box_occupancy.py` dan dueño o adoptan antes de acreditar, conservan los asserts de atribución, y los tests de lifecycle son aditivos. `test_loopback.py` y `test_instance_fence.py` no cambiaron.

## FAMILIAS

- Hallazgo ALTA (`repair_manifest_recovery`): familia visitada, **invariante-atada-al-call-site**, con efecto de **identidad/autoridad del destino**. Es una recurrencia exacta del hueco que este cierre decía haber eliminado: el helper protege release, pero repair conserva una transición hermana directa.
- Hallazgo MEDIA (lectura durante rollback): familia visitada, **sello-vs-dato**. El escritor ya está cercado, pero el lector aún puede combinar el dato post-rollback con el sello pre-tumba.
- Hallazgo MEDIA (cerco retenido tras retire): familia **NUEVA**, ciclo de vida asimétrico del cerco —alta sin baja en los terminales físicos—. No es borrado-sin-tumba: aquí sobrevive una marca lógica que debió morir con su binding/run.
- Hallazgo BAJA (`exec_enforce`): familia visitada, **identidad/autoridad del destino**; el control acierta, pero el protocolo colapsa el motivo exacto.
- No apareció una familia nueva de coherencia de caché, frontera de generación ni borrado-sin-tumba.

## BACKLOG

- El gate/oráculo está fuera del producto, pero sus huecos explican el verde espurio: N25 sólo discrimina `release_owner`; no fuerza `repair_manifest_recovery` entre snapshot y commit del poll. La suite tiene un test de repair que lleva el run no reconocido a `EXITED`, no el caso estable `RUNNING → RUNNING_IDLE` (`../ws/tools/tests/test_process_lifecycle.py:2078-2111`).
- El test de compensación comprueba escritor-durante-replace y lectura después de la tumba (`test_process_lifecycle.py:2261-2319`), pero no un lector entre `replace(target)` y `_forget_attempt_activity`.
- `gate/run.sh` dio `ORACULO: PASS=35 FAIL=0 UNMET=0 de 35`; `gate/suite.sh` dio `SUITE-ACOTADA OK` (76 + 109 + 8 + 65 + 45 + 46 tests). No propongo tocar gate, oráculo ni ledger en este lote.

## NO VERIFICADO

- No ejecuté un puente Enforce/DayZ real ni tráfico HTTP real; las cuatro reproducciones son deterministas in-process sobre las clases productivas y sus locks reales.
- No ejecuté la suite completa del repositorio; sólo el oráculo y la suite acotada que acompañan este lote, más las sondas específicas descritas.
- `rg` no encontró callers de `release_all_running_owners` dentro de `../ws/tools`; no verifiqué consumidores externos, imports dinámicos ni plugins fuera del workspace.
- No hice stress probabilístico de deadlocks. El dictamen de locks es un recorrido estático de todos los `with self._operation_lock`, `with self._activity_lock`, `with self._lock` y de los callbacks directos entre las dos clases, complementado por intercalaciones deterministas.
