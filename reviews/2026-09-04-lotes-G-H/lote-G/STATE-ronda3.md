## HECHO

La actividad deja de leerse del audit. Vive en `_last_activity` / `_activity_unknown`, indexada por `(daemon_generation, run_id)`. El audit sigue registrando un `run_command_activity` en el enqueue aceptado.

- N4 sustrato: `_run_activity` (`tools/dayz_mcp/process_lifecycle.py:969-982`) lee solo memoria. Un jsonl corrupto o ausente no mueve el estado.
- N1/N5 selección: `record_box_command_activity` (`:927-949`) ya no corta el fallback. Si `owner_session` no casa un único dueño y hay un solo run activo (p.ej. `RUNNING_IDLE`), ese run recibe la actividad. Varios activos sin dueño coincidente: no se marca ninguno.
- N2: `enqueue_command(..., internal=True)` propaga `internal` a `_enqueue_command` / `_enqueue_exec_enforce` (`tools/dayz_mcp/loopback.py:1296-1305`, `:1476-1486`, `:1517-1526`, `:1568-1570`, `:1684-1686`) y no llama a `_note_run_command_activity`.
- N3: `_write_command_activity` (`:951-967`) anula `_box_cache` vía `_invalidate_box_cache` (`:901-904`) **después** de soltar `_activity_lock`, sin tomar `_operation_lock`.
- R5 intacto: el enqueue aceptado sigue escribiendo el evento (`:953-960`, reason=`enqueue_accepted`). R1 intacto: `box_occupancy` no escribe.

RETIRADO (no quedó inalcanzable):

| símbolo | era | consumidores al borrar |
|---|---|---|
| `_latest_jsonl_activity_epoch` | `:991` | solo `_run_activity` |
| `_ensure_activity_persisted` | `:979` | ninguno (huérfano desde ronda 2) |
| `_jsonl_activity_written` | `:830` | solo `_write_command_activity` |
| `_activity_persist_attempted` | `:829` | `_write_command_activity` + `_ensure_activity_persisted` |
| `_audit_jsonl_path` | `:903` | los dos anteriores |

Grep tras el retiro, en `tools/` (py+md): **cero** matches de esos cinco nombres y de `activity_observed`. El único resto del árbol es `gate/GATES.md:89-90,108` (sello de ronda 2, fuera del write-set).

Tests: reescritos `test_rotated_jsonl_without_event_is_unknown` y `test_rotated_jsonl_without_commands_is_unknown` (no rotaban: vaciaban el ledger). `JsonlActivityLookupTest` adaptada al sustrato en memoria. No toqué `test_process_lifecycle.py`.

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
[PASS ] N1-SIN-DUENO un run RUNNING_IDLE comandado SI registra actividad
          estado_run=['RUNNING_IDLE'] activity='recent' edad=10.0 (esperada ~10.0) -- 'stale' con la edad del arranque es el defecto P1
[PASS ] N2-INTERNO un enqueue interno (cleanup) NO registra actividad de comando
          status=200 llamadas_al_registro=0 -- si es 1, abandonar la caja se anota como uso reciente
[PASS ] N3-CACHE tras un fallo de escritor la lectura cacheada no dice recent/stale
          cacheado='unknown' -- loopback.py llama al lector SIN `now`, que es justo la rama cacheada; publicar frescura ahi rompe el sticky fail-closed
[PASS ] N5-REINICIO heredado es unknown y una actividad nueva lo lleva a recent
          heredado='unknown' tras_actividad='recent' -- si el segundo sigue en unknown, la clausula es inalcanzable
========================================================================================================
ORACULO: PASS=18 FAIL=0 UNMET=0 de 18
ORACULO-VERDE
```

```
test_box_occupancy : Ran 56 tests in 0.848s  OK
test_process_lifecycle : Ran 83 tests in 0.875s  OK
test_lifecycle_http : Ran 8 tests in 4.762s  OK
test_loopback : Ran 64 tests in 0.792s  OK
test_mcp_tools : Ran 45 tests in 7.232s  OK
test_session_coordination : Ran 46 tests in 0.064s  OK
SUITE-ACOTADA OK
```

(`bash gate/run.sh` y `bash gate/suite.sh`; python del venv citado en `gate/suite.sh`.)

## CONTROL POSITIVO

Corridos contra el motor **sin** el arreglo, mismo intérprete. Fallos (AssertionError literal):

- `test_corrupt_jsonl_does_not_change_activity` — `'unknown' != 'recent'`
- `test_cleared_jsonl_does_not_change_recorded_activity` — `'unknown' != 'recent'`
- `test_unowned_running_idle_command_records_activity` — `'stale' != 'recent'`
- `test_internal_enqueue_does_not_call_activity_recorder` — `1 != 0`
- `test_internal_enqueue_does_not_write_run_command_activity` — `1 != 0` (len after vs before)
- `test_writer_fault_invalidates_cached_occupancy` — `'stale' != 'unknown'`
- `test_inherited_generation_is_unknown_until_new_activity` — `'unknown' != 'recent'` (el heredado ya era unknown; falló el paso a recent: N1)
- `JsonlActivityLookupTest.test_accredited_start_is_basal_without_commands` — `'unknown' != 'recent'`
- `test_forged_jsonl_activity_does_not_change_state` — `'unknown' != 'recent'`
- `test_cleared_jsonl_keeps_basal_activity` — `'unknown' != 'recent'`

Verdes **antes** del arreglo, y por qué no acreditan el cambio:

- `test_multiple_unowned_runs_do_not_all_receive_box_activity` — ok. El targeting viejo ya no marcaba nada si `owner_session` no casaba; el test cierra que el fallback N1 no rocía a todos.
- `test_box_activity_targets_only_the_owned_run_among_several` — la 1ª versión falló `'unknown' != 'stale'` en run-2 **porque run-2 no tenía basal** (store.add, no start). Eso no mide N1. Le añadí `record_command_activity("run-2", now=_activity_at(0.0))` y **no** volví a correr esa versión contra el motor viejo.

## LO QUE NO PUDE VERIFICAR

- La suite completa (2321 tests) está prohibida en este lote.
- No reejecuté el arnés de saturación Windows (`os.replace` + handle abierto). El lector ya no existe; la carrera se disuelve por construcción, no por una medición nueva.
- No hay test de estrés concurrente del orden `_activity_lock` vs `_operation_lock`. La guarda es de inspección: `_invalidate_box_cache` no toma `_operation_lock`.
- `test_box_activity_targets_only_the_owned_run_among_several` en su forma final no tiene traza rojo-antes (ver CONTROL POSITIVO).
- No toqué `README-mcp.md` (el brief lo reserva al integrador).
- `test_process_lifecycle.py` está en el write-set y no cambió: no hay tests de actividad ahí.

## DISPUTAS

Ninguna. El oráculo N4 exige que el estado **no cambie** (no que sea `recent`); en esta corrida `sano='stale'` por las dos anclas (creación del proceso vs reloj del start_outcome), y corromper/borrar no lo movió.
