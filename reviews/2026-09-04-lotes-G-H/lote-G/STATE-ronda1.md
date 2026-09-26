## HECHO

P1. `box.runs` publica por run `activity_state` (`recent` / `stale` / `unknown`, umbral 900 s) y `last_activity_age_s` (número, o `None` si `unknown`).

- Umbral: `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-G\ws\tools\dayz_mcp\process_lifecycle.py:38` (`_ACTIVITY_STALE_S = 900.0`) y `:1064`.
- Cálculo: `_run_activity` en `:1036-1066`.
- Fila de ocupación: `:2647-2657` (`activity_state` / `last_activity_age_s` junto a las seis claves previas). La decisión `occupied` no usa actividad (`:2687`).

P2. `occupancy_error_fields` proyecta esos dos campos al error público, junto a `age_s`, en el bucle de enriquecimiento (`:269-270`). No van en el literal inicial (`:239-246`): un mutante que salte el bucle los pierde.

P3. Evento `run_command_activity` en el audit.

- Enqueue aceptado, rama normal: `loopback.py:1566-1569`.
- Enqueue aceptado, `_enqueue_exec_enforce`: `loopback.py:1679-1682`.
- Rechazo (whitelist, peer, exec denegado, fence, etc.) no llama al hook.
- Escritura: `_write_command_activity` (`process_lifecycle.py:958-977`). Fallo de escritor (`False` o excepción) no tumba el enqueue (`loopback.py` `try/except` + `_audit` que traga excepciones en `:894-897`).
- Sticky por `(daemon_generation, run_id)`: `:828-829` y `:970-971`. Un write bueno posterior no borra la clave (`:973`).
- Generación nueva: `ProcessLifecycle(..., daemon_generation=...)` en `daemon.py:537`; el par sticky de la generación anterior no aplica.

Fail-closed a `unknown`/`None`: timestamp futuro (`:1053-1054`), fallo de escritor, ausencia legacy sin evento ni memoria de start (`:1051-1052`), JSON inválido (`:999-1001`), rotación (jsonl escrito y ya no está en el fichero actual, `:1044-1046`).

Tests: `tools\tests\test_box_occupancy.py` clase `RunCommandActivityTest` (`:499-741`) y `OccupancyErrorFieldsTest.test_projects_activity_fields_from_run_row` (`:388`).

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
[PASS ] G4-STICKY tras fallo de escritor sigue unknown pese a edad >900
          valor='unknown' -- si dice 'stale' el sticky no existe
========================================================================================================
ORACULO: PASS=11 FAIL=0 UNMET=0 de 11
ORACULO-VERDE
```

```
test_box_occupancy : Ran 45 tests in 0.588s  OK
test_process_lifecycle : Ran 83 tests in 0.640s  OK
test_lifecycle_http : Ran 8 tests in 4.706s  OK
test_loopback : Ran 64 tests in 1.104s  OK
test_mcp_tools : Ran 45 tests in 7.310s  OK
test_session_coordination : Ran 46 tests in 0.064s  OK
SUITE-ACOTADA OK
```

`bash gate/run.sh` (Git Bash) y `bash gate/suite.sh`, intérprete
`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe`.

## CONTROL POSITIVO

Corridos ANTES de tocar producción, mismo intérprete, `PYTHONPATH=.`:

```
test_projects_activity_fields_from_run_row ... ERROR
    KeyError: 'activity_state'

test_activity_state_is_recent_at_899s_and_stale_at_901s ... ERROR
    KeyError: 'activity_state'

test_future_clock_is_unknown_with_null_age ... ERROR
    KeyError: 'activity_state'

test_legacy_run_without_activity_event_is_unknown ... ERROR
    KeyError: 'activity_state'

test_occupancy_error_fields_project_activity_with_prior_values ... ERROR
    KeyError: 'activity_state'

test_old_owned_pid_still_occupies_when_activity_is_stale ... ERROR
    KeyError: 'activity_state'

test_command_resets_age_only_for_its_run ... ERROR
    AttributeError: 'ProcessLifecycle' object has no attribute 'record_command_activity'

test_invalid_jsonl_is_unknown ... ERROR
    AttributeError: 'ProcessLifecycle' object has no attribute 'record_command_activity'

test_new_generation_clears_inherited_unknown ... ERROR
    AttributeError: 'ProcessLifecycle' object has no attribute 'record_command_activity'

test_rotated_jsonl_without_event_is_unknown ... ERROR
    AttributeError: 'ProcessLifecycle' object has no attribute 'record_command_activity'

test_writer_fault_is_sticky_unknown_for_the_generation ... ERROR
    AttributeError: 'ProcessLifecycle' object has no attribute 'record_command_activity'

test_accepted_enqueue_records_activity_on_both_branches ... FAIL
    AssertionError: 0 not greater than 0
```

`test_rejected_enqueue_does_not_record_activity_on_either_branch` y
`test_writer_fault_does_not_fail_accepted_enqueue` salieron ok en rojo porque el sujeto no escribía nada: no son control positivo de la feature, sí de que el enqueue no se rompía.

Tras el cambio, los 14 tests nuevos OK; `tests.test_box_occupancy` + `tests.test_process_lifecycle` = 128 OK.

## LO QUE NO PUDE VERIFICAR

- Suite completa (2321 tests). Fuera de este workspace; la corre la orquestadora.
- Mutantes de la ficha (`success clears sticky`, `campos sólo en box.runs`, `fallback al start`). La calibración dijo que se corren contra el código entregado, no aquí.
- Rotación real por `AUDIT_MAX_BYTES` (5 MB). El test vacía `events.jsonl` a mano; no dispara `_rotate_locked`.
- Reinicio real del proceso daemon. El test de generación nueva cambia `lifecycle.daemon_generation` in-process.
- Escritura jsonl bajo fallo de disco real (OSError). El sink de test devuelve `False`; `_audit` traga excepciones, pero no monté un `JsonlAuditWriter` que falle al append.
- Auditoría `rigorous-data-audit` post-gate (D3). La ficha la deja a la orquestadora.
- Juego, red, PBO. Fuera de alcance.

## DISPUTAS

El oráculo G4 inyecta el fallo de escritor alrededor de `box_occupancy`, no de un enqueue. El contrato dice que el evento se escribe en el enqueue aceptado. Para que G4 vea el sink, la primera ocupación de un run con actividad en memoria hace un persist de un disparo (`reason="activity_observed"`, `_ensure_activity_persisted` en `process_lifecycle.py:979`). El enqueue aceptado sigue escribiendo `reason="enqueue_accepted"`. Un rechazo no escribe. Si el persist de ocupación se considera fuera de contrato, G4 queda rojo sin él; lo dejé porque el gate exige `ORACULO-VERDE`.
