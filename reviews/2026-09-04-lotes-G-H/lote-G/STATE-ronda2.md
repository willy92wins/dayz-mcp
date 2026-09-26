## HECHO

El lookup de actividad ya no escribe. `_run_activity` (`tools/dayz_mcp/process_lifecycle.py:1048`) deja de llamar a `_ensure_activity_persisted`; va directo a `_latest_jsonl_activity_epoch` (`:1053`). `box_occupancy` / `occupancy_error_fields` no tocan el audit.

La basal durable es el último `lifecycle_start_outcome` con `decision="started"` y `state="RUNNING"` para ese `run_id` y `daemon_generation`, leído del jsonl (`:1013-1017`). La edad sale de su `timestamp_utc` (`:1038-1039`), no de `creation_time_utc`. Un `run_command_activity` aceptado posterior sigue ganando si su epoch es más reciente (`:1028-1043`).

Un evento de actividad (comando o start acreditado) sin `daemon_generation` no se infiere: `_latest_jsonl_activity_epoch` devuelve `"unusable"` (`:1022-1024`) y `_run_activity` proyecta `unknown`/null (`:1055-1056`). Otra generación se salta (`:1025-1026`); si no queda evidencia de la generación viva y hay jsonl, `"missing"` ⇒ `unknown` (`:1059-1060`), no fallback a memoria. El fallback a `_last_activity` queda solo cuando no hay jsonl (`"absent"`, `:1061-1063`): ahí `_capture_start_activity` (`:913`, sembrada en el start en `:1593`) sigue siendo la basal en memoria.

`_write_command_activity` (`:958`) sigue siendo la única escritura de `run_command_activity`, desde el enqueue aceptado (`record_command_activity`). `_ensure_activity_persisted` (`:979`) ya no está en el camino de lectura; no lo borré.

Tests: `tools/tests/test_box_occupancy.py` — sticky conducido por enqueue aceptado (`test_writer_fault_is_sticky_unknown_for_the_generation:552`, `test_new_generation_clears_inherited_unknown:564`); clase nueva `JsonlActivityLookupTest:750` con lectura byte a byte (`:845`), basal 899/901 s (`:852`), evento sin generación (`:862`) y rotación sin comandos (`:883`). `test_process_lifecycle.py` no hizo falta.

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
          escribio 0: eventos=[] reasons=[] -- la ficha pide lookup read-only
[PASS ] R2-BASAL sin comandos, el start ACREDITADO da recent/stale a 899/901 s
          899s='recent' 901s='stale' -- si los dos dicen 'stale', la edad se esta midiendo desde creation_time_utc del proceso y no desde el evento auditado
[PASS ] R3-HEREDADO un run de OTRA generacion queda unknown
          valor='unknown' -- el start acreditado es de gen-A y el daemon corre gen-B
[PASS ] R4-AMBIGUA un evento sin daemon_generation no se toma por actual
          valor='unknown' -- 'recent' significa que un evento sin generacion se acepto como de la generacion viva; la ficha manda unknown, nunca inferencia
[PASS ] R5-DURABLE un comando aceptado deja UN evento y la edad se mide de el
          rec=True eventos=1 estado='recent' edad=200.0 (esperada ~200.0)
========================================================================================================
ORACULO: PASS=16 FAIL=0 UNMET=0 de 16
ORACULO-VERDE
```

```
test_box_occupancy : Ran 49 tests in 1.771s  OK
test_process_lifecycle : Ran 83 tests in 1.348s  OK
test_lifecycle_http : Ran 8 tests in 4.851s  OK
test_loopback : Ran 64 tests in 2.203s  OK
test_mcp_tools : Ran 45 tests in 13.551s  OK
test_session_coordination : Ran 46 tests in 0.163s  OK
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos contra el motor de la ronda 1, antes del arreglo de lectura/basal/generación:

- `test_occupancy_lookup_does_not_write_audit` (`test_box_occupancy.py:850`): `AssertionError: b'{"e[1591 chars]A"}\n' != b'{"e[1591 chars]A"}\n{"event":"run_command_activity","reason":[178 chars]"}\n'`
- `test_accredited_start_is_basal_without_commands` (`:855`): `AssertionError: 'stale' != 'recent'`
- `test_activity_event_without_generation_is_unknown` (`:880`): `AssertionError: 'recent' != 'unknown'`

Después de quitar la escritura en el lookup, y antes de fall-closed en `"missing"`:

- `test_rotated_jsonl_without_commands_is_unknown` (`:888`): `AssertionError: 'stale' != 'unknown'`

Los dos sticky reescritos (`test_writer_fault_is_sticky_unknown_for_the_generation`, `test_new_generation_clears_inherited_unknown`) ya pasaban contra el motor viejo una vez conducidos por `record_command_activity`; no son control positivo del cambio de esta ronda, son para que la suite no caiga al dejar de escribir desde la lectura.

## LO QUE NO PUDE VERIFICAR

- Suite unittest completa del árbol: prohibida; solo `gate/suite.sh` (6 módulos).
- Reinicio real de proceso: el jsonl se leyó en el mismo proceso con `JsonlAuditWriter`, no un daemon nuevo que abra el fichero tras un crash.
- Rotación real de `JsonlAuditWriter._rotate_locked`: el test vacía `events.jsonl` a mano.
- Fallo de escritor sobre `JsonlAuditWriter.write` (siempre `True`); el sticky se midió con `AuditSink.fail_events`.
- `occupancy_error_fields` no tiene sink; comprobé que no muta el jsonl al proyectar un box ya leído, no una ruta de escritura propia.
- `_ensure_activity_persisted` sigue en el módulo y seguiría escribiendo si alguien la llamara. No verifiqué que no quede ninguna llamada fuera de este archivo (grep en el write-set: solo su definición).
- No corrí in-game / dayz-mcp.

## DISPUTAS

Ninguna sobre el criterio del gate de esta ronda.
