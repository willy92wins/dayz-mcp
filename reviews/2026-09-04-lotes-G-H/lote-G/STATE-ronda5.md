## HECHO

Coherencia de la caja: la actividad se recalcula en cada lectura; la revisión sube en toda mutación que cambia la caja; sin binding se publica `unknown`; la frescura no retrocede.

**P1 — la actividad no se cachea.** `box_occupancy` (`process_lifecycle.py:2580-2605`) sigue cacheando sondas/argv. Cada retorno pasa por `_with_live_activity` (`:1007-1022`) que pisa `activity_state` / `last_activity_age_s` desde `_last_activity` / `_activity_unknown`. Quien pierde la carrera contra una mutación (N9) ya no se lleva el snapshot viejo.

**P2 — revisión en toda mutación de caja.** `_bump_box_revision_locked` (`:902-906`) incrementa y anula `_box_cache`. Desde `_operation_lock` se llama `_invalidate_box_cache` (`:908-910`), que solo toma `_activity_lock`.

Lista completa de call-sites que suben la revisión:

| Sitio | path:line |
|---|---|
| `_capture_start_activity` (basal de arranque, tras `manifest.replace` OK) | `:932` |
| `record_box_command_activity` sin `run_id` (olvida sellos → `unknown`) | `:966` |
| `_write_command_activity` (comando acreditado o sticky de escritor) | `:985` |
| `_settle_failed_launch` restaura previous | `:1265` |
| `_settle_failed_launch` deja UNRECONCILED | `:1285` |
| `start_run` alta del provisional `STARTING` | `:1482` |
| `stop_run` UNRECONCILED (proceso unknown) | `:1820` |
| `stop_run` STOPPING | `:1877` |
| `stop_run` UNRECONCILED (cuarentena a mitad) | `:1892` |
| `stop_run` UNRECONCILED (terminate fallido) | `:1934` |
| `stop_run` EXITED | `:1984` |
| `adopt_run` RUNNING | `:2062` |
| `release_owner` RUNNING→RUNNING_IDLE | `:2071` |
| `begin_release_owner` release sin unacknowledged | `:2093` |
| `begin_release_owner` identity mismatch → UNRECONCILED | `:2143` |
| `begin_release_owner` terminate fallido → UNRECONCILED | `:2170` |
| `begin_release_owner` EXITED | `:2184` |
| `begin_release_owner` `release_owner` residual | `:2189` |
| `repair_recovery_fault` EXITED | `:2289` |
| `repair_manifest_recovery` swap del manifiesto | `:2327` |
| `repair_manifest_recovery` EXITED por run | `:2377` |
| `_reap_run_locked` (ya estaba, test propio) | `:2451` |
| `admin_reconcile` | `:2863` |

Grep que lo prueba (sobre `tools/dayz_mcp/process_lifecycle.py`):

```
902:     def _bump_box_revision_locked(self) -> None:
908:     def _invalidate_box_cache(self) -> None:
910:             self._bump_box_revision_locked()
932:             self._bump_box_revision_locked()
966:             self._bump_box_revision_locked()
985:             self._bump_box_revision_locked()
1265:                 self._invalidate_box_cache()
1285:                 self._invalidate_box_cache()
1482:             self._invalidate_box_cache()
1820:                 self._invalidate_box_cache()
1877:                 self._invalidate_box_cache()
1892:                             self._invalidate_box_cache()
1934:                             self._invalidate_box_cache()
1984:                 self._invalidate_box_cache()
2062:             self._invalidate_box_cache()
2071:                 self._invalidate_box_cache()
2093:                     self._invalidate_box_cache()
2143:                                     self._invalidate_box_cache()
2170:                                     self._invalidate_box_cache()
2184:                         self._invalidate_box_cache()
2189:                         self._invalidate_box_cache()
2289:             self._invalidate_box_cache()
2327:             self._invalidate_box_cache()
2377:                 self._invalidate_box_cache()
2451:         self._invalidate_box_cache()
2863:             self._invalidate_box_cache()
```

**P3 — sin binding, `unknown`.** `record_box_command_activity` sin `run_id` (`:954-966`) no acredita por dueño ni por cardinalidad: borra los sellos de los runs activos. Loopback (`loopback.py:1717-1729`) sigue entregando por `_legacy_queues` y llama esa rama cuando no hay `run_id` de binding.

**P4 — frescura monótona.** `_write_command_activity` (`:982-984`) solo escribe si `current is None or epoch >= current`.

**(A)** alta `STARTING` sube revisión en `:1482`, antes de `self.launcher(...)`.

**(B)** `_capture_start_activity` se movió a **después** del `manifest.replace` de RUNNING (`:1550-1563`). Un `start_run` rechazado en ese replace no siembra basal; `_settle_failed_launch` restaura el manifiesto y sube revisión (`:1265/:1285`).

`ack_run` no sube: `launch_acknowledged` no es ocupación.

## GATE

`bash gate/run.sh`:

```
========================================================================================================
[PASS ] G1-899s activity_state es 'recent'
          valor='recent' fila=['activity_state', 'age_s', 'label', 'last_activity_age_s', 'mod', 'owner_session', 'run_id', 'state']
[PASS ] G1-901s activity_state es 'stale'
          valor='stale' fila=['activity_state', 'age_s', 'label', 'last_activity_age_s', 'mod', 'owner_session', 'run_id', 'state']
[PASS ] G1-EDAD last_activity_age_s es un numero
          valor=899.0 tipo=float
[PASS ] G2-activity_state se proyecta a occupancy_error_fields
          claves=['activity_state', 'age_s', 'foreign', 'hint', 'label', 'last_activity_age_s', 'mod', 'occupied_by_run_id']
[PASS ] G2-last_activity_age_s se proyecta a occupancy_error_fields
          claves=['activity_state', 'age_s', 'foreign', 'hint', 'label', 'last_activity_age_s', 'mod', 'occupied_by_run_id']
[PASS ] G2-age_s sigue en la proyeccion, sin regresion
          claves=['activity_state', 'age_s', 'foreign', 'hint', 'label', 'last_activity_age_s', 'mod', 'occupied_by_run_id']
[PASS ] G2-CONTROL las seis claves previas del error publico, con su VALOR
          faltan=[] run_id='run-1' mod='@SameMod' age_s=899.0 hint_no_vacio=True
[PASS ] G2-CONTROL las seis claves previas de box.runs siguen
          faltan=[]
[PASS ] G2-CONTROL un PID owned antiguo sigue ocupando la caja
          occupied=True runs=1
[PASS ] G3-FUTURO un reloj anterior al arranque da unknown
          valor='unknown'
[PASS ] G4-STICKY una escritura buena posterior NO limpia el sticky
          valor='unknown' escritura_buena=True -- si dice 'stale', un exito posterior borro el fallo
[PASS ] R1-READONLY una lectura de box_occupancy no escribe en el audit
          escribio 0: []
[PASS ] R5-RASTRO un comando aceptado sigue dejando UN evento en el audit
          rec=True eventos=1 estado='recent' -- el audit deja de ser la fuente de verdad, no deja de registrar
[PASS ] N4-SUSTRATO corromper o borrar events.jsonl no cambia la actividad
          sano='stale' corrupto='stale' borrado='stale' -- si cambian, el audit sigue siendo la fuente de verdad y vuelve la carrera
[PASS ] N1-ATRIBUCION la actividad se acredita al run del BINDING
          status=200 eventos=['run-2'] -- 'run-1' significa que el destino se reconstruye por dueno/cardinalidad y el binding exacto se tira; [] significa que un run sin dueno no registra nada
[PASS ] N2-INTERNO un enqueue interno (cleanup) no acredita a ningun run
          status=200 eventos=[] -- el cleanup por expiracion de lease sellaria como recien usado el run recien abandonado
[PASS ] N3-CACHE tras un fallo de escritor la lectura cacheada no dice recent/stale
          cacheado='unknown' -- loopback llama al lector SIN `now`, la rama cacheada
[PASS ] N5-REINICIO heredado es unknown y una actividad nueva lo lleva a recent
          heredado='unknown' tras_actividad='recent'
[PASS ] N6-GENERACION extender un run heredado no importa un sello pre-daemon
          estado='stale' edad=6000.0 -- si la edad se mide desde un proceso anterior a este daemon, la generacion nueva acredita una continuidad que no tiene
[PASS ] N7-CACHE-START tras start_run la lectura cacheada ve la caja ocupada
          start_ok=True cache_previa=False cacheado=True -- publicar 'vacia' tras un arranque invita a que otro arranque pise una sesion viva
[PASS ] N8-CACHE-CARRERA un lector que calculo antes no publica tras la invalidacion
          verdad_directa='unknown' publicado_por_la_cache='unknown' -- si difieren, el fallo de escritor no queda fail-closed hasta que caduque el TTL
[PASS ] N9-FRESCA quien pierde la carrera DEVUELVE la actividad posterior, no la vieja
          devuelto='unknown' -- si dice recent/stale, la respuesta que recibio ese cliente ya era falsa al emitirse
[PASS ] N11-SIN-BINDING un enqueue sin binding no acredita frescura a nadie
          status=200 eventos=[] estados={'run-1': 'unknown', 'run-2': 'unknown'} -- 'recent' en alguno significa que se acredito por cardinalidad, sin prueba del destino; 'stale' significa que se dejo envejecer una sesion viva
[PASS ] N12-MONOTONA una escritura tardia no hace retroceder la actividad
          edad=100.0 (esperada ~100.0; 1100.0 significa que la escritura vieja gano y la frescura retrocedio)
[PASS ] N13-ESTRUCTURAL la caja cacheada no publica un run ya parado
          cacheado=False directo=False -- si cacheado sigue True, la sesion que acaba de parar su run ve su propia caja ocupada por un run EXITED, con el hint de pararlo otra vez
========================================================================================================
ORACULO: PASS=25 FAIL=0 UNMET=0 de 25
ORACULO-VERDE
```

`bash gate/suite.sh`:

```
test_box_occupancy : Ran 66 tests in 1.310s  OK
test_process_lifecycle : Ran 90 tests in 0.982s  OK
test_lifecycle_http : Ran 8 tests in 4.738s  OK
test_loopback : Ran 64 tests in 0.895s  OK
test_mcp_tools : Ran 45 tests in 10.200s  OK
test_session_coordination : Ran 46 tests in 0.067s  OK
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos **antes** del cambio de producción, contra el código que fallaba. 11/11 rojos por el motivo del producto, no por import/typo.

**(A) P1 — alta STARTING no subía revisión** — `test_starting_provisional_invalidates_empty_occupancy_cache`:

```
AssertionError: False is not true : {'occupied': False, 'runs': [], 'foreign': [], 'ports_in_use': [], 'queue': [], 'scan_known': True}
```

El manifiesto ya tenía el provisional `STARTING`; la lectura cacheada seguía vacía.

**(B) MAYOR — start_run rechazado dejaba frescura** — `test_failed_extend_does_not_leave_freshness_on_inherited_run`:

```
AssertionError: 'recent' != 'unknown'
```

El extend falló al persistir RUNNING; el run heredado restaurado publicaba `recent` por la basal sembrada antes del replace.

**N9** — `test_losing_race_returns_live_activity_not_pre_mutation_snapshot`:

```
AssertionError: 'stale' != 'unknown'
```

**N11** — `test_unbound_enqueue_publishes_unknown_without_crediting`:

```
AssertionError: 'stale' != 'unknown'
```

run-1 se dejó envejecer (stale) en vez de `unknown`.

**N12** — `test_late_activity_write_does_not_regress_freshness`:

```
AssertionError: 'stale' != 'recent'
```

La escritura de epoch 1000 pisó la de 2000; edad ~1100.

**N13** — `test_stop_run_invalidates_occupancy_cache`:

```
AssertionError: True is not false
```

**adopt / release_owner / repair / begin_release_owner / admin_reconcile** — la caché devolvía el estado anterior a la mutación:

```
AssertionError: 'RUNNING_IDLE' != 'RUNNING'     # adopt
AssertionError: 'RUNNING' != 'RUNNING_IDLE'     # release_owner
AssertionError: True is not false               # repair_recovery_fault (occupied)
AssertionError: True is not false               # begin_release_owner (occupied)
AssertionError: 'UNRECONCILED' != 'RUNNING_IDLE' # admin_reconcile
```

Preservación (ya verdes antes de este cambio; no son evidencia de arreglo nuevo): `test_start_run_invalidates_empty_occupancy_cache`, `test_slow_occupancy_reader_does_not_republish_after_invalidation`, `test_reap_invalidates_box_occupancy_cache`, los 21 checks previos del oráculo.

Los tests que llamaban `record_box_command_activity` sin `run_id` y esperaban crédito por cardinalidad/dueño se alinearon a P3 (`unknown`, o crédito por `record_command_activity` con `run_id`).

## LO QUE NO PUDE VERIFICAR

- Un lector **ya dentro** de `_box_occupancy_uncached` cuando se da de alta el `STARTING` sigue pudiendo **devolver** (no recachear) el snapshot estructural vacío: P1 solo pisa campos de actividad. El daño del waiter FIFO es la lectura siguiente; esa sí ve `STARTING` porque no recacheamos lo vacío. No instrumenté un diag_probe lento concurrente con el alta para devolver ese snapshot in-flight.
- N12 se midió con dos escrituras secuenciales de epoch distinto, no con dos hilos del `ThreadingHTTPServer` reales. La guarda `epoch >= current` es la misma comparación.
- `ack_run` no sube revisión (ocupación no cambia). No hay test de caché para ese camino.
- No corrí DayZ in-game ni la suite completa de `tools/tests` (prohibido).
- No verifiqué `repair_manifest_recovery` con un test de caché propio; el bump está, el test cubre `repair_recovery_fault`.

## DISPUTAS

Ninguna. El oráculo pide `unknown` sin binding (no `stale` ni crédito por cardinalidad) y los cuatro rojos N9/N11/N12/N13 cierran el producto P1–P4.
