# Revisión adversarial ciega — Opus (familia Anthropic)

Producto verificado por hash antes y después de la sesión (no toqué `ws/`):

```
2195ca23cc57b011e856950f0bd7769ba99ba7ea0e5dc58fcf6dc85b3541ea04 *ws/tools/dayz_mcp/process_lifecycle.py
52ee08f83cb79f59c8343f4ce24b8c792a3dd8f4404ef5c865c706674c8651cb *ws/tools/dayz_mcp/loopback.py
```

Gate, corrido por mí, última línea de cada uno:

```
bash ws/gate/run.sh    ->  ORACULO: PASS=29 FAIL=0 UNMET=0 de 29 / ORACULO-VERDE
bash ws/gate/suite.sh  ->  SUITE-ACOTADA OK
```

Sondas propias (todas en `review6-opus\`, ninguna toca `ws/`):
`probe_h1_revision_skew.py`, `probe_h1_ablacion.py`, `probe_h1_stress.py`,
`probe_h2_p3_sin_binding.py`, `probe_h3_p5_rollback.py`,
`probe_p4_retire_vs_enqueue.py`, `probe_ventana_audit.py`.

---

## VEREDICTO

Verde el gate, rojo el contrato: tres roturas ejecutables y deterministas (5/5) —
P1/P2 por sesgo de instante entre `runs` y `revision`, y P3 y P5 por un borrado sin
tumba cuyo escritor rival muestrea su `epoch` 6–16 ms antes de comprometerlo; P4 y
el orden de locks aguantan.

---

## HALLAZGOS

### H1 — [ALTA] La revisión que sella las sondas se lee en otro instante que el manifiesto que certifica

**Fallo concreto.** Una sesión lee la caja como **vacía** (`occupied=False`,
`foreign=[]`) mientras un DayZDiag ajeno sigue vivo, y arranca encima.

**Mecanismo.** `_take_box_snapshot` toma las dos mitades de la foto en instantes
distintos:

```
process_lifecycle.py:2668  def _take_box_snapshot(self, clock: float) -> _BoxSnapshot:
process_lifecycle.py:2669      runs = tuple(self.manifest.list_runs())     # <- instante t0
process_lifecycle.py:2670      with self._activity_lock:
process_lifecycle.py:2686          revision = self._box_revision           # <- instante t1 > t0
```

`revision` no es un sello de `runs`: es una cota **superior**. Si una mutación
durable cae entre 2669 y 2686, el lector se queda con el manifiesto de t0 y con la
revisión de t1. Después calcula las sondas con ese manifiesto viejo —
`registered_pids` sale de `snapshot.runs` (2697) y filtra los PID observados
(2732)— y las guarda bajo la revisión nueva, porque el guardián de escritura
compara contra esa misma cota:

```
process_lifecycle.py:2766      probes = self._collect_probes(snapshot)
process_lifecycle.py:2769          if self._box_revision == snapshot.revision:
process_lifecycle.py:2770              self._box_cache = (time.monotonic(), snapshot.revision, probes)
```

A partir de ahí, durante hasta `_BOX_OCCUPANCY_CACHE_S` = 1,5 s, **cualquier
lector con la foto correcta del manifiesto** acierta en ese cache y compone filas
nuevas con sondas viejas. Y `occupied` sale de las dos mitades a la vez:

```
process_lifecycle.py:264   occupied = True if not probes.scan_known else bool(runs or probes.foreign)
```

Filas vacías (verdad nueva) + `foreign` vacío (sondas viejas, que filtraron el PID
por estar registrado en el manifiesto viejo) = **caja vacía**.

**Repro** — `probe_h1_revision_skew.py`, barrera determinista en el seam
`manifest.list_runs` sólo para el hilo lector, mutación por `stop_run` público.
Escenario honesto: PID reciclado; el guard da identidad completa distinta →
`_classify_registered_process` lo marca `foreign` (1790-1791), `stop_run` no
termina nada (`terminate_calls == []`) y lleva el run durablemente a EXITED. El
DayZDiag del PID 48976 no era nuestro y sigue vivo.

```
[antes ] occupied=True runs=['run-existing:RUNNING'] foreign=[] scan_known=True
[mutador] stop_run -> {'ok': True, 'run_id': 'run-existing', 'state': 'EXITED', 'terminated': 0}
[R     ] occupied=True runs=['run-existing:RUNNING'] foreign=[] scan_known=True
[R2    ] occupied=False runs=[] foreign=[] scan_known=True
[verdad] occupied=True runs=[] foreign=[{'port': 2302, 'mods': ['@Foreign'], 'profiles': 'other'}] scan_known=True
VEREDICTO-H1: ROTO      (5/5 corridas)
```

Dos roturas en la misma traza: **R** publica una fila `RUNNING` de un run ya
EXITED durable (el N13/N18 bajo concurrencia), y **R2** —un lector limpio, con el
manifiesto correcto— publica la caja vacía.

**Ablación (control positivo del mecanismo)** — `probe_h1_ablacion.py`. Único
cambio: leer `_box_revision` **antes** de `list_runs()`, para que la revisión sea
cota *inferior*. Con eso 2769 rechaza la escritura del cache y la ventana se
cierra:

```
[producto ] R2 occupied=False foreign=[]                  | verdad occupied=True foreign=[{...}] | coincide=False
[ablacion ] R2 occupied=True  foreign=[{port 2302, ...}]  | verdad occupied=True foreign=[{...}] | coincide=True
```

La causa queda localizada en el orden 2669/2686, no en el TTL ni en `_derive_box`.

**Consumidor.** `box_occupancy` tiene un solo llamador en el producto y usa la
ruta con cache: `loopback.py:244-247` (`_box_payload`, "for /session/status only").
De ahí sale al bucle de espera de caja (`server.py:2501 _box_from_status`), que
es lo que decide si una sesión puede arrancar. Es decir: el camino envenenado es
exactamente el que se consulta para tomar la caja.

**Anchura medida, honestamente** — `probe_h1_stress.py` (sin barreras, 3 lectores
contra 120 ciclos add/stop, ×3): **0 mentiras en 220.967 lecturas cacheadas**. La
ventana es de anchura de preempción entre dos sentencias contiguas; con barrera es
100 % reproducible, sin barrera no la vi caer en este equipo. No está acotada por
nada del diseño (disco más lento, manifiesto mayor, más sesiones la ensanchan),
pero su probabilidad natural aquí es baja. Por eso ALTA y no CRÍTICA.

---

### H2 — [ALTA] Una escritura acreditada dentro del audit resucita el sello que una observación sin binding posterior ya borró (P3)

**Fallo concreto.** P3 exige que una observación sin binding **posterior** deje el
run en `unknown`. Publica `recent`.

**Mecanismo.** `_write_command_activity` muestrea su `epoch` en el llamador
(1002 y 1012), pasa por el audit **fuera de todo lock** y sólo entonces toma
`_activity_lock`:

```
process_lifecycle.py:1036  def _write_command_activity(self, run_id, epoch, *, reason) -> bool:
process_lifecycle.py:1038      ok = self._audit(...)          # <- E/S, fuera de lock
process_lifecycle.py:1046      with self._activity_lock:
process_lifecycle.py:1051          if current is None or epoch >= current:
process_lifecycle.py:1052              self._last_activity[key] = epoch
```

El borrado del lado sin binding es marca de agua **sin tumba**:

```
process_lifecycle.py:1024      runs = list(self.manifest.list_runs())
process_lifecycle.py:1028      with self._activity_lock:
process_lifecycle.py:1032          if current is not None and current <= epoch:
process_lifecycle.py:1033              self._last_activity.pop(key, None)
```

Nada registra "a partir de T no admito sellos ≤ T". El orden que decide es el de
los **commits**, no el de los `epoch`: una escritura acreditada de T-30 s que
estaba dentro del audit cuando pasó la observación de T-5 s vuelve a sembrar el
sello y la caja publica frescura que la observación había revocado.

Los dos hilos no son hipotéticos: son los dos que `loopback` ya usa.
`_note_run_command_activity` (`loopback.py:1736`) llama a `record_command_activity`
para un enqueue con binding y a `record_box_command_activity` para uno sin él, y
lo hace **fuera** del lock de estado (`loopback.py:1607-1610`, `1727-1731`).

**Repro** — `probe_h2_p3_sin_binding.py`, barrera en el sink de audit sobre el
evento `run_command_activity`:

```
[secuencial] tras enqueue con binding                          -> ('recent', 30.0)
[secuencial] tras observacion sin binding POSTERIOR            -> ('unknown', None)   <- N16, verde
[carrera   ] tras observacion sin binding (acreditada en audit)-> ('unknown', None)
[carrera   ] tras salir la acreditada del audit                -> ('recent', 30.0)    <- P3 ROTO
VEREDICTO-H2: ROTO      (5/5 corridas)
```

**Anchura medida** — `probe_ventana_audit.py`. La ventana no es una preempción:
`JsonlAuditWriter._append_payload_locked` (`runtime_state.py:339-349`) **relee el
fichero entero y lo reescribe atómico**, bajo un lock global de escritor
(`runtime_state.py:254`, `259`). Medido en disco local:

```
AUDIT_MAX_BYTES = 5242880
events.jsonl ~    0 KB -> write() mediana  6,22 ms  max  9,34 ms
events.jsonl ~  256 KB -> write() mediana  8,97 ms  max 11,00 ms
events.jsonl ~ 1024 KB -> write() mediana  8,14 ms  max 10,39 ms
events.jsonl ~ 4096 KB -> write() mediana 11,64 ms  max 16,32 ms
```

6–16 ms sin lock, creciendo con el tamaño del audit hasta 5 MiB, más la cola del
lock del escritor si hay otro evento en vuelo. Esta sí es una ventana ancha.

---

### H3 — [ALTA] La misma escritura sobrevive al rollback de un arranque fallido (P5)

Misma causa raíz que H2, otra cláusula y otro sitio de borrado.
`_settle_failed_launch` olvida por marca de agua, también sin tumba:

```
process_lifecycle.py:1309      self._forget_attempt_activity(provisional.run_id, attempt_started_at)
process_lifecycle.py:1076  def _forget_attempt_activity(self, run_id, attempt_started_at) -> None:
process_lifecycle.py:1078      with self._activity_lock:
process_lifecycle.py:1080          if current is not None and current >= attempt_started_at:
process_lifecycle.py:1081              self._last_activity.pop(key, None)
```

Si la escritura acreditada en la ventana del intento (`attempt_started_at` se fija
en `process_lifecycle.py:1550`) todavía está dentro del audit cuando corre el
olvido, éste no encuentra nada que borrar y el sello aterriza después. El run
restaurado a RUNNING publica `recent`.

**Repro** — `probe_h3_p5_rollback.py`: dos barreras, una en el launcher (para
estar seguros de haber pasado 1550) y otra en el sink de audit. Control secuencial
equivalente: `tests/test_process_lifecycle.py:773`
(`test_failed_extend_discards_activity_credited_in_the_confirm_window`), que pasa.
Aquí sólo se mueve el instante del commit.

```
[start  ] -> {'error': 'lifecycle_start_failed', '_http_status': 503, 'run_id': 'run-existing', 'state': 'RUNNING'}
[durable] state=RUNNING procesos=1                                   <- rollback correcto
[rollback, acreditada aun en el audit] -> ('RUNNING', 'unknown', None)
[tras salir la acreditada del audit  ] -> ('RUNNING', 'recent', 0.003)   <- P5 ROTO
VEREDICTO-H3: ROTO      (5/5 corridas)
```

---

## INTENTOS SIN HALLAZGO

Cero hallazgos aquí es el resultado, no una omisión.

**P4 — ningún binding despachable sobrevive a la salida durable de RUNNING.**
Ataqué la ventana entre `_retire_run_bindings` (`process_lifecycle.py:1930`) y la
transición durable (`1933`) con barrera sobre `manifest.replace` envuelto
(`probe_p4_retire_vs_enqueue.py`, 5/5 sin romper):

```
[antes  ] enqueue -> (200, ...)
[ventana] manifiesto durable=RUNNING enqueue -> (409, {'error': 'binding_retired', ...})
[despues] enqueue -> (409, {'error': 'binding_retired', ...})
```

Aguanta por dos defensas independientes y en el orden correcto: el retire y el
enqueue comparten el `_lock` de `ServerState` (`loopback.py:1006`, `1121-1153`), y
además el enqueue consulta el estado **durable** antes de aceptar
(`_bound_run_has_left_running`, `loopback.py:1081-1101`). Revisados por lectura
los seis sitios del contrato: `stop_run` 1862/1864 y 1930/1933,
`begin_release_owner` 2207/2209, 2235/2237, 2254/2255, `repair_recovery_fault`
2356/2358, `repair_manifest_recovery` 2444/2446, `_reap_run_locked` 2517/2519,
`admin_reconcile` 2949/2951. En todos el retire precede al write, y si el write
falla queda retirado con el run vivo — fail-closed, no al revés.

Nota sobre `begin_release_owner`: la rama temprana (`2160-2162`) hace
`manifest.release_owner` RUNNING → RUNNING_IDLE **sin** retirar bindings, y
`_bound_run_has_left_running` deja pasar RUNNING_IDLE (`loopback.py:1097-1101`).
No lo cuento como defecto: el proceso sigue vivo y adoptable, y retirarlo rompería
`adopt_run`. La tolerancia no es un defecto.

**Orden de locks.** No hay inversión `_activity_lock → _operation_lock`. Los
mutadores toman `_operation_lock` y luego `_activity_lock` (comentado en `967-969`);
el lector nunca toma `_operation_lock` (`box_occupancy` 2651-2666). Ninguna de las
nueve secciones críticas de `_activity_lock` (`974-975`, `989-997`, `1028-1034`,
`1046-1053`, `1060-1063`, `1078-1082`, `2670-2686`, `2757-2758`, `2768-2770`)
llama a nada que tome `_operation_lock` ni al manifiesto. Tampoco hay
`manifest._lock → _activity_lock`: el `checkpoint` que corre bajo el lock del
manifiesto (`633-658`) es el recovery store (`daemon.py:415,442`), no el loopback.

**E/S bajo lock.** Ninguna. El audit de `_write_command_activity` corre antes del
lock (1038 vs 1046) —que es justo lo que abre H2/H3— y la reescritura del
manifiesto de `repair_manifest_recovery` (2384-2390) corre bajo `_operation_lock`,
no bajo `_activity_lock`.

**¿Ha desaparecido alguna validación o rechazo?** Por inspección del fichero
actual (no por diff; ver NO VERIFICADO) siguen en pie y las ejercité o las leí una
por una: `binding_retired` 409 con su hint (ejercitado arriba); `scan_known=False
→ occupied=True` (264, y `_collect_probes` 2721-2727); sello futuro
(`stamp > clock`) → `unknown` (235-236 y 1066-1067); `_activity_unknown` pegajoso
que una escritura buena **no** limpia (1049); filtro por `daemon_generation` en la
foto (2671-2685) y en la clave (963-965); negativa explícita a acreditar por dueño
o por cardinalidad (1022-1026); rechazo por diag ajeno en `start_run`
(`_foreign_diag_reason` 1472-1479); `empty_box(occupied=True)` como único
fallback (`server.py:2503,2506,2620`) y el `except` de `_box_payload`
(`loopback.py:248-255`), ambos fail-closed.

**P2 — mutación durable sin incremento del contador.** Las filas se releen en
cada lectura (2669), así que se publican igual: verificado. Busqué el caso que sí
importaría —una mutación durable **sin** bump que cambie `registered_pids` o el
conjunto activo, porque eso sí reutilizaría sondas incoherentes sin necesidad de
carrera— y no existe. Las dos mutaciones durables sin bump que encontré,
`ack_run` (`1702-1706`, sólo `launch_acknowledged`) y
`RunManifestStore.quarantine_legacy_active` (`705-764`, RUNNING → UNRECONCILED
conservando `processes`, estado que sigue en `_ACTIVE_STATES`), no tocan ni el
conjunto activo ni los PID registrados. Igual `recover_after_restart` al final de
`repair_manifest_recovery` (2453).

---

## FAMILIAS

Pregunta de proceso: ¿familia nueva o de las tres ya visitadas (coherencia de
caché, identidad del destino, frontera de generación)? **Las dos son nuevas**, y
ninguna se arregla con las palancas de las tres viejas.

**F-A (H1) — el sello de invalidación se toma en otro instante que el dato que
sella.** No es coherencia de caché: el cache se invalida correctamente y el TTL es
correcto. Lo que falla es que el *token* que certifica una foto se lee después de
la foto, así que certifica un estado que nunca vio. Bajar el TTL, invalidar más
veces o cachear menos no lo cierra; lo cierra ordenar la lectura del token
(demostrado por la ablación). Adyacente a "coherencia de caché" y distinta de
ella: el defecto vive en el *sello*, no en el *dato*.

**F-B (H2+H3) — el borrado no deja tumba y el escritor muestrea su instante antes
de la E/S.** El destino está bien atribuido (no es "identidad del destino") y la
generación está bien filtrada (no es "frontera de generación"). Lo que falla es
que el sistema razona con el orden de los `epoch` mientras el estado se decide por
el orden de los *commits*, y entre uno y otro hay 6–16 ms de audit sin lock. Las
dos cláusulas rotas (P3 y P5) y sus dos sitios de borrado (`1032-1033` y
`1080-1081`) son la misma familia: marca de agua sin tumba. Dos usos más del mismo
patrón quedan expuestos aunque no los ejecuté (ver NO VERIFICADO).

Consecuencia de proceso: el gate cubre ambas familias **en su forma secuencial**
(N15, N16, N18, N13, y `test_process_lifecycle.py:773`) y las tres pasan. Las
formas concurrentes no tienen puerta. Un gate de esta clase necesita al menos una
puerta con barrera inyectada por cada seam que ya está inyectado para los tests.

---

## BACKLOG (fuera de producto según el brief)

1. `RunManifestStore.list_runs` (`659-662`) clona cada `RunRecord` con
   `from_payload(asdict(run))` en cada llamada, y `box_occupancy` se llama en cada
   poll de `/session/status`. Coste, no corrección.
2. Copias `.bak_*` de `process_lifecycle.py` y `loopback.py` dentro del paquete
   importable `dayz_mcp/` (4 ficheros, hasta 126 KB). Ruido de árbol; ninguna se
   importa.
3. `JsonlAuditWriter._append_payload_locked` relee y reescribe el fichero entero
   por evento (`runtime_state.py:346-349`): O(n²) sobre el audit y, de paso, lo que
   ensancha la ventana de H2/H3. Fuera de producto (`runtime_state.py`), pero es
   la palanca más barata para estrechar F-B si no se quiere tocar el diseño.

---

## NO VERIFICADO

- **No hice diff contra ninguna revisión anterior.** La pregunta "¿ha desaparecido
  alguna validación?" está contestada por inspección del fichero actual contra el
  contrato del brief, no por comparación con un estado previo. Un rechazo que
  existiera antes y no esté en el contrato §0 se me habría escapado.
- **`argv_of` y `diag_probe` fueron inyectados en todas mis sondas.** No ejecuté
  nada contra `native_process_snapshot` ni contra procesos DayZDiag reales; la
  correspondencia entre lo que devuelve el probe real y lo que asumo (`{"known":
  True, "processes": [{"pid": ...}]}`) la tomé de la forma que exige
  `_diag_snapshot` (2627-2649), no de una corrida real.
- **H1 sin barreras: 0 de 220.967 lecturas.** No medí la frecuencia natural en el
  daemon real (disco de la carpeta runtime, manifiesto con varios runs, varias
  sesiones). Mi número es un límite de este equipo y de este banco, no del
  producto en producción.
- **Segundo vector de F-B, razonado pero NO ejecutado (hipótesis):**
  `record_box_command_activity` lee `manifest.list_runs()` en `1024`, fuera de
  `_activity_lock` (`1028`). Un run añadido por `manifest.add` entre ambas líneas
  no está en `active` y escapa al borrado, aunque su sello —sembrado por
  `_capture_start_activity` (`1631`) con la hora de creación del proceso, que puede
  ser anterior— sea ≤ epoch. Misma causa raíz que H2; no escribí el repro.
- **Tercer vector, razonado y NO ejecutado (hipótesis):** el sesgo de H1 también
  puede entrar por `repair_manifest_recovery` (`2384-2390`), que sustituye
  `self.manifest` entero por un backup que puede tener MENOS runs que el vivo,
  dejando PID vivos sin registrar. No lo monté; el repro que sí corre usa
  `stop_run`.
- **Alcance leído.** Sólo `process_lifecycle.py` y `loopback.py` completos.
  `server.py`, `dayz_test_*`, `runtime_state.py` y `session_coordination.py` los
  abrí únicamente en los puntos que cito, para nombrar el consumidor y medir la
  ventana del audit; no los revisé.
- **No corrí la suite completa**, sólo los seis módulos de `gate/suite.sh`.
- **No leí** nada bajo `review*/`, `runs*/`, `ws-frozen-r6/` ni `ws/STATE.md`. Sí
  leí `gate/run.sh` y `gate/suite.sh` (para saber qué corría) y
  `tests/test_process_lifecycle.py` / `tests/test_box_occupancy.py` (para construir
  fixtures fieles). No leí `gate/oracle.py`.
