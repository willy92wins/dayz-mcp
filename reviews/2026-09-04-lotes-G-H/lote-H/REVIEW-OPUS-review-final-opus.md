# REVIEW-OPUS — revisión adversarial ciega de la composición G+H

Revisor: Claude Opus 5 (familia Anthropic). Ciego: no se ha abierto nada bajo
`review*/`, `runs*/` ni `ws/STATE.md`. Sólo el producto fusionado, los tres
gates y el árbol congelado `lote-G\ws-frozen-r8\tools\`.

## Identidad del producto verificada

```
70179879d1a0af8e0d2adcf625cd9d1e3d46ee8a7e56a5476dd52ed89729c6e0 *ws/tools/dayz_mcp/process_lifecycle.py
d607e62e8f37f8ea8a3d6169a8c5e333f7f158ab1cc2cec353a02ad721d3230e *ws/tools/dayz_mcp/loopback.py
bca97e26e3a7d9cef96846bee6559eebc938dbbd90e46b3eab6b0f75019f4b5d *ws/tools/dayz_mcp/dayz_test_tool.py
```

Los tres casan con el brief (`sha256sum`, 2026-09-04). 3362 / 3783 / 938 líneas.

## Gates corridos (última línea, textual)

| Gate | Comando | Última línea |
|---|---|---|
| Oráculo H | `bash ws/gate/run.sh` | `ORACULO-VERDE` (precedida de `ORACULO: PASS=40 FAIL=0 UNMET=0 de 40` y `ORACULO-CENSO OK: 40 checks, ninguno repetido`) |
| Oráculo G | `python ../../gate-extra/oracle_lote_g.py` desde `ws\tools` | `ORACULO-VERDE` (precedida de `ORACULO: PASS=42 FAIL=0 UNMET=0 de 42`) |
| Suite acotada | `bash ws/gate/suite.sh` | `SUITE-ACOTADA OK` (11 módulos, 456 tests, todos OK) |

## VEREDICTO

**APROBADO con dos notas de severidad BAJA.** La composición G+H aguanta las
intercalaciones ejecutables que le he lanzado: nueve sondas con barreras
deterministas sobre seams inyectados (`manifest.list_runs`, `manifest.replace`,
`manifest.release_owner`, `manifest.get`, `bindings.retire_run`,
`atomic_write_bytes`, `_operation_lock` instrumentado) más un estrés de cinco
hilos, y **no he encontrado ninguna celda fail-open alcanzable, ninguna lectura
rasgada, ningún interbloqueo, ninguna validación desaparecida respecto de
`ws-frozen-r8` y ninguna fuga de path o timestamp en el sobre ni en el anillo**.

Los dos hallazgos son de endurecimiento y de alcance de una afirmación; ninguno
es una regresión de H ni bloquea la entrega. Cero hallazgos de severidad ALTA o
MEDIA. Sondas en `review-final-opus\probes\`.

Lo que sí queda medido y verde, con su repro:

- El cerco cierra la **única ventana en que decide solo** — run durablemente
  `RUNNING` **con dueño**, cerco levantado, transición sin persistir: enqueue
  `409 run_not_owned`, poll entrega `[]`, la caja no publica frescura
  (`p4_ventana_del_cerco.py`, caso A). Con el `persist` lanzando, el cerco se
  revierte y el despacho vuelve (caso B).
- La matriz completa **6 estados durables × cercado/no × mutación / lectura /
  interno = 36 celdas**: ninguna fail-open, ni por enqueue ni por poll
  (`p5_fail_open_matriz.py`). Estado ilegible → `503 run_state_unavailable`.
- La **lectura rasgada de H9 no se puede forzar** ni parando al lector entre el
  manifiesto y el anillo, ni desarmando el reintento
  (`_STATUS_SNAPSHOT_TRIES=1`): lo que la sostiene no es el sello de revisión
  sino el filtro `live` de `process_lifecycle.py:1095-1099`
  (`p2_status_vs_retire.py`).
- El **sobre alimentado con el payload real** del producto sale de las dos
  ramas (fila y anillo) con exactamente sus claves, sin paths ni timestamps, y
  cae a `ToolError` pelado en cuanto el anillo es ambiguo
  (`p7_sobre_desde_producto_real.py`).
- El anillo: 32 exactos, desalojo por la cola, ocho claves exactas, vacío tras
  reiniciar el lifecycle sobre el mismo manifiesto; legacy → `null`, nunca
  `false` (`p8_anillo_y_legacy.py`).
- **Orden de locks**: instrumentando `_operation_lock`, ni `_status_snapshot`,
  ni `box_occupancy`, ni `status()`, ni el crédito bajo el lock del loopback lo
  toman jamás (`p9_locks_y_es.py`, A). Cinco hilos concurrentes 6 s
  (98.503 enqueues, 130 despachos reales, 5 ciclos release/adopt): sin
  interbloqueo, sin despacho sin dueño, sin fila viva conviviendo con su
  diagnóstico (`p6_orden_de_locks.py`).
- **Diff contra `ws-frozen-r8`**: 0 `def` eliminados, 0 clases eliminadas,
  0 códigos de error eliminados, y las 14 constantes de gobierno
  (`RUN_STATES`, `_ACTIVE_STATES`, `_REAPABLE_STATES`, `_ACTIVITY_STALE_S`,
  `_BOX_OCCUPANCY_CACHE_S`, `MAX_QUEUE`, `MAX_RESULTS`, `COMMAND_TTL_S`,
  `RETIRED_INSTANCE_LIMIT`, `CLIENT_COMMANDS`, …) idénticas valor a valor,
  comparadas **importando ambos árboles**, no leyendo el fuente.

## HALLAZGOS

### H-1 · BAJA · `_commit_retirement` no aísla sus tres pasos post-persist: un fallo en `retire_run` deja EXITED en disco, el anillo mudo y el cerco levantado para siempre

`process_lifecycle.py:1048-1062`:

```python
    def _commit_retirement(self, run, event, reason, decision) -> bool:
        # Persist first; only then retire bindings and publish the diagnostic.
        try:
            self.manifest.replace(run)
        except Exception:
            return False
        self._invalidate_box_cache()
        binding_reason = _BINDING_REASON_BY_EVENT.get(event, reason)
        self._retire_run_bindings(run.run_id, binding_reason)   # <- sin guarda
        self._retire_run_diagnostic(run, event, reason, decision, "EXITED")
        self._forget_run_residues(run.run_id)
        return True
```

El orden persist→publicar es el correcto y H7 lo gatea. Lo que no está cubierto
es **qué pasa después del punto de no retorno**. `_retire_run_bindings`
(`process_lifecycle.py:1012-1016`) llama a `bindings.retire_run` **sin
try/except**, a diferencia de `_prepare_instance` (`:987-993`), `_unfence_runs`
(`:1582-1590`) y el `unfence` de `_forget_run_residues` (`:1229-1235`), que sí
lo tienen. Si ese paso lanza:

1. el manifiesto ya tiene `EXITED` en disco;
2. `_retire_run_diagnostic` no llega a correr → **el anillo no tiene
   diagnóstico** de esa retirada;
3. `_forget_run_residues` no llega a correr → **quedan residuos de actividad** y
   el cerco sigue levantado;
4. y como el run ya es `EXITED`, **ningún camino vuelve a pasar por él**:
   `_REAPABLE_STATES` (`:39`) no incluye `EXITED`, `_RECOVERY_REPAIR_STATES`
   (`:282-284`) tampoco, y `admin_reconcile` exige `UNRECONCILED/STARTING/
   STOPPING` o `RUNNING_IDLE` ownerless (`:3265-3273`). La marca lógica vive
   hasta el reinicio del daemon — exactamente el daño que N28 nombra.

**Repro (corre):** `probes\p3_stop_replace_falla.py`, parte 2. Run cercado por
`release_owner`, `bindings.retire_run` inyectado para lanzar, reaper:

```
reap -> None excepcion: RuntimeError('discard_committed reventado')
durable: EXITED
[ROTO ] P3d el diagnostico se pierde aunque EXITED ya esta en disco: estado=EXITED anillo=[]
[ROTO ] P3e el cerco sobrevive a un run ya EXITED: {'run-B'}
[ROTO ] P3f residuos de actividad no limpiados en EXITED: {'tombstone': [('gen-A', 'run-B')], ...}
2o paso del reaper: [] anillo: 0            <- no converge nunca
```

Y `p5_fail_open_matriz.py` confirma el efecto observable del cerco huérfano:
la fila `EXITED / cerco=True` responde `run_not_owned` en vez de
`binding_retired` — sigue siendo fail-closed, pero con el motivo equivocado.

**Alcanzabilidad — lo que NO he verificado.** He recorrido el camino completo de
`ServerState.retire_run` (`loopback.py:1023-1037` → `_retire_instance_locked`
`:1070-1099` → `_discard_queue` `:2315` → `_mark_discarded` `:2353` →
`_flush_queue_discards` `:1128-1139` → `_finish_operations` `:2393-2414` →
`SessionCoordinator.discard_committed`) y **todas las llamadas externas de ese
camino están envueltas**: `_tombstone_run_activity` tiene try/except
(`loopback.py:1166-1178`), el bucle de `exec_audit` también (`:1133-1139`), y el
audit del coordinador captura con `except Exception: return "failed"`
(`session_coordination.py`, `_write_audit_outcome_locked`), mientras el callback
de cleanup corre en un hilo aparte con su propio try/except
(`_release_active_locked`). **No he encontrado ningún disparador en árbol.** Por
tanto esto es endurecimiento estructural con repro sobre seam inyectado, no un
defecto de producción demostrado. Coste del arreglo: envolver los tres pasos
post-persist en su propio `try/except` (o invertir a
`diagnóstico → residuos → bindings`), unas cinco líneas.

### H-2 · BAJA · La razón declarada de `_status_snapshot` sobrepasa lo que el código garantiza: /status sí espera la escritura de un stop

`process_lifecycle.py:1080-1082`:

```python
        # Never takes _operation_lock: /status is the health discriminator and
        # must not wait out a stop. Revision stamp + bounded retry, then drop
        # a diagnostic whose run still has a non-terminal row in this payload.
```

La primera mitad es cierta y la he verificado instrumentando el lock: en
`p9_locks_y_es.py` (A) el hilo lector hace 20 × (`_status_snapshot` +
`box_occupancy` + `status`) y el contador de tomas de `_operation_lock` por hilo
sale `ninguno`. Pero la conclusión que el comentario saca de ahí —"no espera a
un stop"— no se sigue: `_status_snapshot` llama a `self.manifest.list_runs()`
(`:1088`), que toma `RunManifestStore._lock` (`:731-733`), y ese mismo lock lo
retiene `replace()` (`:755`) **durante `_persist_locked` → `atomic_write_bytes`**
(`:705-726`), es decir durante E/S a disco.

**Repro (corre):** `probes\p9_locks_y_es.py`, parte B. Se parquea el `stop_run`
dentro de `atomic_write_bytes` sobre `runs.json` y se cronometra un
`_status_snapshot` concurrente:

```
[ROTO ] P9b /status se queda esperando la escritura del stop: bloqueado_mas_de_0.7s=True dt=0.7256602000052226
```

No es un fallo de corrección: la exposición está acotada por **una** escritura
atómica (milisegundos en disco sano) y el resto del stop —terminate, snapshots
del guard, audit— sí queda fuera. Pero el comentario, tal como está, hace de
una exclusión acotada ("no toma `_operation_lock`") una exclusión general ("no
espera a un stop"), y es exactamente el tipo de frase que un lector futuro usará
para justificar meter más E/S bajo `RunManifestStore._lock`. Arreglo: precisar
el comentario, no el código.

## FAMILIAS

Pregunta del brief: ¿familia nueva o de las visitadas? **Ninguna nueva.**

- **H-1 → «ciclo de vida asimétrico del cerco» (visitada).** El cerco se levanta
  por un camino guardado y revertible (`_transition_to_idle`, `:1592-1613`:
  fence → persist → confirmar, con `_unfence_runs` en el `except` y en el
  `leftover`) y se baja por una cadena sin guarda ni reintento
  (`_commit_retirement`). La asimetría no está en quién lo levanta o lo baja
  —eso el contrato lo cubre— sino en que **el estado terminal es absorbente**:
  cuando falla la bajada, no queda ninguna máquina que vuelva a mirar ese run.
  Sub-caso, no familia nueva: la compensación post-commit no es reintentable
  porque el productor de reintentos (`reap_dead_runs`) filtra por estados no
  terminales.
- **H-2 → «un negativo acotado no es un negativo general» (visitada,
  `bounded-negative-is-not-a-general-negative`).** «No toma este lock» se
  escribió como «no espera». El instrumento que valida la primera mitad
  (contador de tomas por hilo) no dice nada de la segunda, y hace falta un
  cronómetro contra un seam de E/S para separarlas.
- **P5c (ver BACKLOG) → «fail-open escondido en la forma adyacente»
  (visitada).** La rama `state is None` de `_enqueue_run_rejection` no rechaza,
  mientras la forma adyacente del poll (`_poll_should_hold_commands`, `:1207-1212`)
  sí retiene sobre el mismo `None`. Dos lecturas del mismo hecho con dos
  defaults opuestos.

No he encontrado casos de **coherencia de caché** (el sello de
`_probes_for_snapshot` `:3135-3150` se compara contra `snapshot.revision` y sólo
se guarda si la revisión no ha cambiado), ni de **sello-vs-dato** (el orden
revisión → manifiesto → actividad de `_take_box_snapshot` `:3039-3073` deja el
sello como cota inferior), ni de **borrado-sin-tumba** (toda retirada y todo
cerco pasan por `_raise_activity_tombstone_locked` `:1238-1243` y
`_seal_activity_locked` rechaza `epoch <= tumba`, con `<=`, no `<`), ni de
**identidad/autoridad del destino**, ni de **frontera de generación** (la clave
de actividad es `(generation, run_id)` y `daemon_generation` no se reasigna en
vida del proceso: sólo se escribe en `daemon.py:351`).

## BACKLOG

Nada de esto es producto de este lote; lo dejo apuntado con su cita.

1. **Rama fail-open residual en `_enqueue_run_rejection`**
   (`loopback.py:1236-1237`): si `_durable_run_state` devuelve `None` el enqueue
   **no rechaza**. Se alcanza sólo si `lifecycle.manifest` no expone `get`
   (arnés/shim); en producción `ProcessLifecycle.manifest` es siempre un
   `RunManifestStore`. Medido en `p5_fail_open_matriz.py` (P5c): enqueue 200
   sobre un run `RUNNING_IDLE`, aunque el poll no entrega nada (0 comandos), o
   sea que la defensa en profundidad aguanta. Coste de cerrarlo: cambiar el
   `return None` por `return "run_state_unavailable"`.
2. **Un stop cuyo `replace` final falla deja el run en `STOPPING`, que no es
   reapable** (`_REAPABLE_STATES`, `:39`). Medido en `p3_stop_replace_falla.py`
   parte 1: `partial_cleanup` 503 + `cleanup_degraded:['manifest_failed']`,
   durable `STOPPING`, y el reaper no converge. **No es un agujero**: el fallo
   se reporta alto y `recover_after_restart` (`:885-908`) convierte `STOPPING`
   en `UNRECONCILED`, que sí es reapable — la convergencia existe, pasa por un
   reinicio del daemon. Merece una línea en la doc de operación, no código.
3. **`daemon.py:573` llama a `manifest.recover_after_restart` directamente**, no
   a través de `_transition_to_idle`, o sea sin cerco. Es inocuo porque al
   arrancar `_fenced_runs` está vacío y no hay bindings a los que despachar, y
   `daemon.py` es BACKLOG explícito del lote. Si algún día ese camino se llama
   en caliente, deja de ser inocuo.
4. **La rama del anillo del sobre es estrecha.** `resolve_stop_run`
   (`dayz_test_tool.py:228-243`) sólo llega a `run_not_found` si la fila ya no está
   en `status()`, y las filas `EXITED` sólo se podan al construir el
   `RunManifestStore` (`:589`, `_prune_exited_on_load`) — es decir al arrancar el
   daemon, que además vacía el anillo. La combinación «fila podada + anillo con
   entrada» se da en vida sólo a través de `repair_manifest_recovery`
   (`:2745-2752`, que construye un store nuevo). Medido en
   `p7_sobre_desde_producto_real.py`: en el caso normal el sobre sale de la
   FILA (`run_not_active`), no del anillo. Funciona, pero conviene saber que el
   camino del anillo es el minoritario.

## NO VERIFICADO

- **In-game.** Nada de esto se ha probado contra un DayZDiag real. Todo es
  proceso Python con `guard`, `retail_probe`, `diag_probe` y `launcher` falsos.
- **`server.py`, `daemon.py`, `runtime_state.py`, los oráculos y los ledgers**:
  BACKLOG por brief, no los he auditado. De `daemon.py` sólo he leído el
  cableado (`:536`, `:573`, `:590`) para reproducir la composición en el banco.
- **Alcanzabilidad de H-1**: recorrida y no encontrada (ver el apartado), pero
  no he demostrado que sea *imposible*; no he auditado `SessionCoordinator`
  entero ni las rutas de fallo de `_purge_box_locked` /
  `_cancel_expired_tickets_locked`.
- **Interbloqueo**: el estrés de `p6_orden_de_locks.py` es empírico (6 s,
  ~98 k enqueues, 5 hilos). Ausencia de cuelgue en esa ventana **no es** prueba
  de ausencia de ciclo de locks. No he hecho análisis estático del grafo de
  adquisiciones ni he metido en el estrés `repair_manifest_recovery`,
  `begin_release_owner` (hilo de cleanup) ni la expiración de leases del
  coordinador, que es donde vive el único candidato a inversión que he visto
  (`_operation_lock → SessionCoordinator._condition` en `_finish_operations`
  frente a `_condition → ServerState._lock` en `cleanup_owner`). Ese camino
  cruza a componentes que son BACKLOG en este lote.
- **`_note_run_command_activity` con `internal=True`**: verificado por lectura
  (`loopback.py:1754-1757` y `:1872-1873`, `activity_epoch` sólo se muestrea
  `if not internal`), no por sonda dedicada.
- **Rendimiento**: no he medido nada más que el bloqueo de P9b. El coste del
  reintento acotado de `_status_snapshot` y el del escaneo lineal del anillo en
  `_retire_run_diagnostic` (hasta 32 comparaciones bajo `_activity_lock`) no se
  han cuantificado.
- **El cambio de comportamiento de `_enqueue_run_rejection` frente a r8**: r8
  dejaba pasar los comandos `internal` y las lecturas sobre un run
  `RUNNING_IDLE` (`ws-frozen-r8/.../loopback.py:1148`, `if mutation and not
  internal:`); H rechaza todo. Es **conforme al contrato** ("sin dueño NADA se
  despacha") y lo he medido en la matriz de P5, pero la consecuencia práctica
  —el `vehicle_release` interno del cleanup de lease puede quedar descartado con
  `run_not_owned`— no la he probado end-to-end. Sí he verificado por lectura que
  el fallo se reporta: `cleanup["cleanup_degraded"] = ["vehicle_release_failed"]`
  (`loopback.py:2612`, `cleanup_owner`). No es silencioso.
