## HECHO

N1/N5 — el enqueue ya no tira el `run_id` del binding. Lo captura bajo `ServerState._lock` y lo pasa a `record_command_activity(run_id)` en las dos ramas; el respaldo por dueño/cardinalidad queda solo si no hay identidad exacta.

- `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-G\ws\tools\dayz_mcp\loopback.py:223-228` `_binding_run_id`
- `:1535-1580` rama normal: `commanded_run_id` bajo el lock, luego `_note_run_command_activity(..., run_id=)`
- `:1645-1701` rama `_enqueue_exec_enforce`, igual
- `:1706-1726` `_note_run_command_activity`: identidad exacta → `record_command_activity`; si no hay, `record_box_command_activity`
- `process_lifecycle.py:936-949` `record_box_command_activity` acepta `run_id=` y no reconstruye destino cuando lo trae

N6 — la basal de una generación nueva es el proceso que este daemon lanza, no `min()` de todos los procesos del run.

- `process_lifecycle.py:912-932` `_capture_start_activity(..., launched=)`: si hay `launched`, solo ese sello; si no, `max(stamps)` (no el más viejo)
- `:1527` `start_run` llama `_capture_start_activity(completed, launched=record)`

N7/N8 — sembrar basal y mutar actividad invalidan con contador de revisión; el lector solo publica si el contador no cambió.

- `process_lifecycle.py:823` `_box_revision`
- `:902-910` bump bajo `_activity_lock` (orden ` _operation_lock → _activity_lock` intacto)
- `:932` captura de basal bumpa
- `:980-985` `_write_command_activity` bumpa en el mismo lock que el sticky
- `:1548` `start_run` ok invalida tras persistir el manifiesto
- `:2408` reap pasa por el mismo invalidator
- `:2547-2563` `box_occupancy` captura revisión, calcula, publica solo si no cambió

Tests: `_bind_state` pasa el `run_id` real (`test_box_occupancy.py:685-688`). Nuevos: atribución por binding con dos runs (`:949`), reinicio por enqueue (`:982`), sello pre-daemon (`:1001` y `test_process_lifecycle.py:652`), caché vacía tras start (`test_box_occupancy.py:188`), carrera de lector lento (`:1030`).

## GATE

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
========================================================================================================
ORACULO: PASS=21 FAIL=0 UNMET=0 de 21
ORACULO-VERDE
```

```
test_box_occupancy : Ran 61 tests in 0.993s  OK
test_process_lifecycle : Ran 84 tests in 0.729s  OK
test_lifecycle_http : Ran 8 tests in 4.677s  OK
test_loopback : Ran 64 tests in 0.895s  OK
test_mcp_tools : Ran 45 tests in 8.211s  OK
test_session_coordination : Ran 46 tests in 0.095s  OK
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos los seis tests nuevos ANTES del cambio de producción (`Ran 6 tests in 10.132s`, `FAILED (failures=6)`):

- `test_accepted_enqueue_attributes_activity_to_binding_run_among_several` → `AssertionError: Lists differ: [] != ['run-2']` (N1: dos activos, el comandado sin dueño, cero eventos)
- `test_inherited_generation_becomes_recent_via_bound_enqueue` → `'unknown' != 'recent'` (N5: el enqueue no acreditaba al run heredado)
- `test_capture_start_activity_does_not_import_pre_daemon_stamp` → `True is not false : ... last_activity_age_s': 3807641.0` (N6: `min(stamps)` era el proceso de 2026-06-01)
- `test_extending_inherited_run_does_not_import_pre_daemon_activity` → `True is not false : ... last_activity_age_s': 8251210.0` (N6 por `start_run`)
- `test_start_run_invalidates_empty_occupancy_cache` → `AssertionError: False is not true` sobre `cached["occupied"]` (N7)
- `test_slow_occupancy_reader_does_not_republish_after_invalidation` → `'stale' != 'unknown'` (N8: el lector lento pisaba la invalidación)

`test_accepted_enqueue_records_activity_on_both_branches` es preservación, no evidencia del arreglo: con un solo run activo ya era verde por cardinalidad; ahora ata `run_id=self.run_id` (`test_box_occupancy.py:687`) y sigue verde por identidad exacta.

## LO QUE NO PUDE VERIFICAR

- La suite completa (~2321 tests): el brief la prohíbe. Solo corrí la acotada del gate y `tests.test_loopback` aparte.
- Todas las intercalaciones de N8. Verifiqué la del oráculo (sonda de diag bloqueada) y la del test entregado (primera sonda lenta, el resto no). Un lector que calcule actividad *después* de mutar y publique un snapshot nuevo no está cubierto: el contador solo impide publicar lo *viejo*.
- N6 por `_capture_start_activity(run)` sin `launched`: el oráculo exige no importar `VIEJO_UTC` (2026-06-01). Con `max(stamps)` la clave de gen-B puede quedarse con el sello de `CREATED_UTC` (2026-07-15), que también es de un proceso anterior a este daemon. El camino real de `start_run` sí sella solo `launched`. El check N6 del gate no distingue esos dos sellos.
- Encolado de verdad sin binding (cola legacy): el fallback por dueño/cardinalidad sigue; no añadí un test de enqueue unbound. Los tests viejos de `record_box_command_activity` cubren el registrador, no ese cableado.
- Deadlock bajo carga. El orden `_operation_lock → _activity_lock` no se invirtió; no lo estresé.
- Runtime in-game / daemon vivo.

## DISPUTAS

Ninguna. El criterio N6 del gate es más estrecho que P2 (solo caza el sello de 2026-06-01); no lo discuto, lo cubrí en el camino `start_run` con `launched=record`.
