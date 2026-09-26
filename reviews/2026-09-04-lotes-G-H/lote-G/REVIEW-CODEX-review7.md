## VEREDICTO

NO es seguro integrarlo: P6 permite despachar sin dueño por dos caminos reproducidos y P7 todavía puede borrar/suprimir un crédito legítimo posterior al rollback durable.

## HALLAZGOS

### ALTA — H1: `RUNNING_IDLE` sólo cerca mutaciones externas; las lecturas y las mutaciones internas atraviesan el binding

`../ws/tools/dayz_mcp/loopback.py:1130-1151`:

```python
    def _run_is_dispatchable(self, run_id: str | None) -> bool:
        """RUNNING or STARTING dispatch; RUNNING_IDLE does not; the rest is retired."""

        return self._durable_run_state(run_id) in {"RUNNING", "STARTING"}

    def _enqueue_run_rejection(
        self, run_id: str | None, *, mutation: bool, internal: bool
    ) -> str | None:
        if not isinstance(run_id, str) or not run_id:
            return None
        if self.lifecycle is None:
            return None
        if self._run_is_dispatchable(run_id):
            return None
        state = self._durable_run_state(run_id)
        if state is None:
            return None
        if state == "RUNNING_IDLE":
            if mutation and not internal:
                return "run_not_owned"
            return None
        return "binding_retired"
```

Fallo concreto: el docstring dice que `RUNNING_IDLE` no despacha, pero el `return None` de la línea 1150 autoriza (a) todo comando de lectura y (b) toda mutación con `internal=True`. Las dos ramas llegan al mismo predicado: el enqueue normal en `loopback.py:1631-1639` y `exec_enforce` en `loopback.py:1705-1715` y `1738-1749`. `exec_enforce` externo sí queda cercado; el agujero está en el resultado del predicado, no en que esa rama lo omita.

Reproducción ejecutada contra los SHA fijados, con un binding `BOUND` cuyo manifiesto devuelve `RUNNING_IDLE`:

```python
s = ServerState("k")
s.lifecycle = SimpleNamespace(manifest=manifest_idle)
s.install_bound_peer(instance=INSTANCE, role="offline", pid=4242, run_id="run-1")

print(s.enqueue_command("query_player_state", {}, peer="server"))
print(s.enqueue_command("world_spawn", {}, peer="server"))
print(s.enqueue_command("vehicle_release", {}, peer="client", internal=True))
```

Resultado observado:

```text
(200, {'id': 1, 'peer': 'server', 'cmd': 'query_player_state'})
(409, {'error': 'run_not_owned', ...})
(200, {'id': 2, 'peer': 'client', 'cmd': 'vehicle_release'})
```

Por tanto, «sin dueño no se despacha» no se cumple y el bridge no queda necesariamente en poll vacío. Además, `_note_run_command_activity()` acredita la lectura aceptada (`loopback.py:1669-1675,1798-1808`), de modo que una petición sin dueño también puede refrescar el diagnóstico de actividad.

Fix sugerido: el estado durable `RUNNING_IDLE` debe producir `run_not_owned` para cualquier comando dirigido a ese run, sin excepción por `mutation` ni `internal`. Si `vehicle_release` necesita completar el cleanup, debe hacerlo y quedar adjudicado antes de publicar `RUNNING_IDLE`; no convertir el enqueue interno en una puerta trasera al cerco.

### ALTA — H2: `release_owner` publica `RUNNING_IDLE` antes de adquirir el lock que vacía la cola; un poll puede ganar y despachar el comando

`../ws/tools/dayz_mcp/process_lifecycle.py:2183-2190`:

```python
    def release_owner(self, session_id: str, lease_id: str) -> list[str]:
        with self._operation_lock:
            self._require_legacy_identity_safe()
            changed = self.manifest.release_owner(session_id, lease_id)
            if changed:
                self._invalidate_box_cache()
                self._drain_pending_for_runs(changed)
            return changed
```

`manifest.release_owner()` ya ha persistido `RUNNING_IDLE` cuando devuelve (`process_lifecycle.py:765-788`). El vaciado posterior entra en otro actor y sólo entonces intenta `ServerState._lock` (`loopback.py:1085-1102`). Mientras tanto, `record_poll()` puede tomar ese lock y consumir la cola. Su segunda pasada valida que la lista no cambió, la autoridad de las mutaciones externas y la cuarentena, pero nunca vuelve a consultar el estado durable del run (`loopback.py:2033-2120`):

```python
            with self._lock:
                queue = deliver_queue
                if (
                    queue is not queue_ref
                    or len(queue) < len(snapshot)
                    or any(
                        queue[index] is not command
                        for index, command in enumerate(snapshot)
                    )
                ):
                    continue
```

`../ws/tools/dayz_mcp/loopback.py:2116-2120`:

```python
                    wire_command = dict(command)
                    wire_command.pop("owner_session_id", None)
                    wire_command.pop("owner_lease_id", None)
                    commands.append(wire_command)
                queue[:] = remaining + queue[len(snapshot) :]
```

La excepción expresa de `vehicle_release` evita incluso el descarte por autoridad (`loopback.py:2094-2107`):

```python
                        elif (
                            command_name == "vehicle_release"
                            and command_id in self._fire_and_forget_ids
                        ):
                            pass
```

Reproducción ejecutada: se encoló el `vehicle_release` interno, `manifest.release_owner()` publicó `RUNNING_IDLE` y se bloqueó justo antes de devolver; el poll ganó al posterior `drain_pending_for_run()`.

```text
queued 200 {'id': 1, 'peer': 'client', 'cmd': 'vehicle_release'}
state RUNNING_IDLE
poll (200, {'commands': [{'id': 1, 'cmd': 'vehicle_release', 'args': {}}],
            'delay_ms': 0, 'bind': 'BOUND'})
```

El mismo intervalo existe en `begin_release_owner()` (`process_lifecycle.py:2208-2213`). H1 hace reproducible también el caso de una lectura pendiente; H2 es independiente de H1 porque el comando puede haber sido admitido legítimamente mientras el run aún era `RUNNING`.

Consecuencia adicional: `adopt_run()` no retira ni recrea el binding —correcto para el reattach—, pero tampoco puede deshacer un comando que ya salió por esta ventana. El binding sobrevive; el cerco no linealiza publicación, vaciado y poll.

Fix sugerido: introducir un estado de cerco lógico en `ServerState`, distinto de retirar físicamente el binding. Activarlo bajo el mismo `_lock` que usan enqueue/poll, vaciar ahí la cola y hacer que poll lo compruebe; sólo después publicar el cambio durable. Si falla la persistencia, revertir el cerco de forma explícita. `adopt_run` debe quitarlo únicamente después de publicar `RUNNING` y confirmar cola vacía.

### MEDIA — H3: la tumba del rollback se fecha después de exponer el rollback y puede borrar un crédito legítimo posterior

`../ws/tools/dayz_mcp/process_lifecycle.py:1320-1337`:

```python
    def _persist_failed_launch_target(
        self,
        target: RunRecord,
        provisional: RunRecord,
        attempt_started_at: float | None,
    ) -> bool:
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
        return False
```

`../ws/tools/dayz_mcp/process_lifecycle.py:1092-1108`:

```python
    def _forget_attempt_activity(
        self,
        run_id: str,
        attempt_started_at: float,
        *,
        rolled_back_at: float,
    ) -> None:
        key = self._activity_key(run_id)
        with self._activity_lock:
            current = self._last_activity.get(key)
            if (
                current is not None
                and attempt_started_at <= current <= rolled_back_at
            ):
                self._last_activity.pop(key, None)
            self._raise_activity_tombstone_locked(key, rolled_back_at)
```

Fallo concreto: `manifest.replace(target)` hace visible y durable el estado restaurado; después se invalida el caché y sólo entonces se muestrea `rolled_back_at`. En ese intervalo un enqueue legítimo ve de nuevo `RUNNING`, acredita actividad y su época queda `<= rolled_back_at`. La compensación la borra y la tumba impide que una escritura concurrente con esa misma época aterrice. La punta superior no es «rollback durable»: es «algún instante posterior al rollback».

Reproducción ejecutada con `replace()` publicando `RUNNING` y acreditando inmediatamente una orden antes de retornar al llamante:

```text
credit_epoch:   1788492626.7064047
surviving stamp: None
tombstone:      1788492626.7064168
```

La generación sí está bien incluida en la clave (`process_lifecycle.py:964-966`) y el censo no encontró escritores alternativos: el basal de start (`process_lifecycle.py:978-998`) y el crédito de comando (`process_lifecycle.py:1054-1069`) convergen en `_seal_activity_locked()`, que consulta la tumba (`process_lifecycle.py:1045-1052`). El defecto es exclusivamente la linealización temporal de la punta superior.

Fix sugerido: la compensación y su tumba deben formar parte de la misma frontera de publicación del rollback. No basta con mover `time.time()` unas líneas: seguirá existiendo un intervalo entre persistencia visible y tumba. Hay que impedir créditos para ese run hasta terminar la compensación (cerco transitorio) o hacer que el store publique estado+tumba mediante una sección crítica/callback que no exponga `RUNNING` entre ambas operaciones.

### COBERTURA SIN HALLAZGO ADICIONAL

- **P6, rama `exec_enforce`:** aplica el cerco antes del audit (`loopback.py:1705-1715`) y lo repite después (`loopback.py:1738-1749`). Para mutación externa sobre `RUNNING_IDLE` reprodujo `409 run_not_owned`. `STARTING` sigue admitido por `loopback.py:1130-1133`.
- **P6, restart sin binding persistido:** dentro de estos dos archivos no hay reconstrucción del binding. Una mutación sin candidatos cae en `legacy_unbound`/`unbound_after_restart` (`loopback.py:1191-1197`), por lo que no encontré despacho mutante por el binding perdido. Las lecturas por cola legacy siguen abiertas y quedan subsumidas por H1.
- **P6, adopción in-process:** `adopt_run()` sólo cambia el manifiesto de `RUNNING_IDLE` a `RUNNING` (`process_lifecycle.py:2122-2181`); no llama a `prepare`, `confirm` ni `retire`, así que rehabilita el mismo binding. La garantía de cola limpia no se sostiene por H1/H2.
- **P7:** todo borrado encontrado eleva tumba: observación sin binding en `process_lifecycle.py:1029-1036` y compensación en `process_lifecycle.py:1100-1108`. La clave es `(daemon_generation, run_id)` (`process_lifecycle.py:964-966`). No encontré escritor que eluda `_seal_activity_locked()`.
- **P8:** la revisión se toma antes de `list_runs()` y de actividad (`process_lifecycle.py:2718-2745`); las sondas sólo se guardan si la revisión sigue idéntica después de calcularlas (`process_lifecycle.py:2805-2823`). No encontré una ruta en estos dos archivos que guarde sondas bajo una revisión posterior al manifiesto que se sondeó.
- **P1-P5:** no encontré otra regresión distinta de H3, que rompe precisamente la punta superior de la compensación P5. La derivación de la caja sigue pura sobre `_BoxSnapshot`+`_BoxProbes` (`process_lifecycle.py:243-272`) y `box_occupancy()` no adquiere `_operation_lock` (`process_lifecycle.py:2701-2716`).

## FAMILIAS

No hay familia nueva.

- H1 y H2 pertenecen a **identidad/autoridad del destino**: el binding físico sigue siendo válido, pero la autoridad durable del run ya no permite despacho; falla el cerco y su linealización con la cola.
- H3 pertenece a **sello-vs-dato** y a la variante ya visitada de **borrado con frontera temporal incorrecta**. Ya hay tumba y respeta generación; lo incorrecto es qué instante certifica como rollback.
- No apareció un nuevo fallo de frontera de generación, caché de sondas ni borrado sin tumba.

## BACKLOG

Ningún hallazgo nuevo fuera del lote. No usé el gate, oráculo, ledger, brief, `runtime_state.py` ni el sobre de `dayz_test_stop` para justificar el dictamen.

## NO VERIFICADO

- No pude determinar históricamente si se debilitó una validación para pasar el gate: el árbol entregado no contiene metadatos Git utilizables (`git rev-parse --show-toplevel` devuelve «not a git repository») ni una revisión anterior de estos dos archivos, y el gate está explícitamente fuera de producto. En el código actual no observé que P6/P7/P8 se consigan omitiendo una validación preexistente, pero eso no prueba el delta histórico.
- No se ejecutó la suite/gate ni una sesión real del daemon/DayZ; las tres reproducciones fueron deterministas e in-process contra las clases de los dos archivos fijados.
- No se verificó wiring externo a estos dos archivos que pudiera reconstruir un binding tras `recover_after_restart`; dentro del alcance fijado esa reconstrucción no existe.
