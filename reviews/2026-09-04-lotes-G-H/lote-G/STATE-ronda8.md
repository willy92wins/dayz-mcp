## HECHO

`tools/tests/test_box_occupancy.py:1006-1010` — en
`test_accepted_enqueue_attributes_activity_to_binding_run_among_several`, entre las
dos mitades, se da dueño a `run-2` (`owner_session_id="A"`, `owner_lease_id="lease-A"`,
`state="RUNNING"`) con `store.replace` antes del `exec_enforce`.

La primera mitad (`camera_get` sobre `run-2` en `RUNNING_IDLE` sin dueño) no se toca:
sigue siendo la lectura de diagnóstico que N1-ATRIBUCION exige. La segunda mitad sigue
siendo una mutación (`exec_enforce`) que debe devolver 200 y acreditar actividad al run
del BINDING (`destinos_exec == ["run-2"]`) entre varios. No se convierte la mutación en
lectura ni se relaja el cerco: `run-2` pasa a tener dueño, que es lo que P6 pide para
despachar. No se usó `adopt_run` porque rechaza con `active_run_exists` mientras `run-1`
sigue activo, y el propósito del test es precisamente «entre varios».

No se tocó `test_process_lifecycle.py`: los tests de la ronda 7 corrieron verdes en este
árbol a la primera.

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
[PASS ] N11-SIN-BINDING un enqueue sin binding no acredita frescura a nadie
          status=200 eventos=[] estados={'run-1': 'unknown', 'run-2': 'unknown'} -- 'recent' en alguno significa que se acredito por cardinalidad, sin prueba del destino; 'stale' significa que se dejo envejecer una sesion viva
[PASS ] N12-MONOTONA una escritura tardia no hace retroceder la actividad
          edad=100.0 (esperada ~100.0; 1100.0 significa que la escritura vieja gano y la frescura retrocedio)
[PASS ] N13-ESTRUCTURAL la caja cacheada no publica un run ya parado
          cacheado=False directo=False -- si cacheado sigue True, la sesion que acaba de parar su run ve su propia caja ocupada por un run EXITED, con el hint de pararlo otra vez
[PASS ] N14-MEZCLA la caja publicada no mezcla dos instantes
          fila={'run_id': 'run-1', 'mod': '@SameMod', 'label': 'gate', 'age_s': 4418392.071, 'owner_session': 'A', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.0} -- las dos fotos coherentes son (state=RUNNING, activity=re
[PASS ] N15-RETROCESO una observacion sin binding mas vieja no borra un sello mas nuevo
          sembrado='recent' despues='recent' -- si cae a 'unknown', una observacion de hace un minuto ha borrado una actividad acreditada posterior, que es exactamente lo que P4 prohibe
[PASS ] N16-SIN-BINDING-MANDA una observacion sin binding posterior si publica unknown
          sembrado='recent' despues='unknown' -- si sigue 'recent', el arreglo de N15 ha desactivado P3 y volvemos a publicar frescura que nadie ha acreditado
[PASS ] N17-BINDING-MUERE-CON-SU-RUN un enqueue tras el EXITED durable se rechaza
          status=409 payload={'error': 'binding_retired', 'hint': 'Target instance was retired (stop, reap, or role replace). Relaunch that role via dayz_test_run so start_run mints a new instance. Resending this command will keep failing until the n
[PASS ] N18-SIN-CACHE-DE-MANIFIESTO la caja publica el estado durable, no el cacheado
          durable='UNRECONCILED' publicado='UNRECONCILED' -- si publica 'RUNNING', las filas salen de una foto vieja y la coherencia depende de haber acertado el incremento del contador en TODAS las mutaciones, incluidas las que aun no se han escrito
[PASS ] N19-SIN-DUENO-NO-DESPACHA un run RUNNING_IDLE no acepta comandos
          status=409 payload={'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt it with dayz_test_run mode=client run_id=... before dispatching mutations.'} -- 200 significa que el daemon acepta una mutacion del mundo par
[PASS ] N20-ADOPCION-REHABILITA tras adopt_run el mismo binding vuelve a despachar
          status=200 payload={'id': 1, 'peer': 'server', 'cmd': 'world_spawn'} -- si no es 200, el arreglo de N19 ha retirado el binding en el release y el reattach exige relanzar el servidor
[PASS ] N21-TUMBA un escritor con epoca anterior al borrado no resucita el sello
          estado='unknown' -- 'recent' significa que la escritura acreditada, muestreada ANTES de la observacion sin binding, aterrizo DESPUES y resucito el sello que P3 habia borrado
[PASS ] N22-SELLO-ANTES-DEL-DATO tras una mutacion en vuelo no se reutilizan las sondas
          sondas tras el lector=2, tras la lectura siguiente=3 -- iguales significa que las sondas calculadas contra el manifiesto de antes de la mutacion se guardaron bajo la revision de despues y un lector limpio las reutiliza: filas nuevas con son
========================================================================================================
ORACULO: PASS=33 FAIL=0 UNMET=0 de 33
ORACULO-VERDE
```

```
test_box_occupancy : Ran 74 tests in 1.010s  OK
test_process_lifecycle : Ran 103 tests in 0.952s  OK
test_lifecycle_http : Ran 8 tests in 4.705s  OK
test_loopback : Ran 65 tests in 0.939s  OK
test_mcp_tools : Ran 45 tests in 7.338s  OK
test_session_coordination : Ran 46 tests in 0.087s  OK
SUITE-ACOTADA OK
```

`test_failed_extend_discards_activity_credited_in_the_confirm_window` (incluido en los 103 de `test_process_lifecycle` arriba), corrido solo:

```
.
----------------------------------------------------------------------
Ran 1 test in 0.022s

OK
```

## CONTROL POSITIVO

Copia temporal `/tmp/r6-control-positivo` desde `../ws-frozen-r6/tools/`, con los dos
ficheros de test actuales. `PYTHONPATH=. python -m unittest tests.<modulo> -k` no: se
invocó cada test por id de clase. Línea de FAIL (o preservación):

- `test_acknowledged_begin_release_owner_rejects_world_spawn` — `AssertionError: 200 == 200 : {'id': 2, 'peer': 'server', 'cmd': 'world_spawn'}`
- `test_admin_reconcile_with_survivors_rejects_world_spawn` — `AssertionError: 200 == 200 : {'id': 1, 'peer': 'server', 'cmd': 'world_spawn'}`
- `test_release_owner_drains_pending_queue_and_adopt_rehabilitates` — `AssertionError: Lists differ: [{'id': 1, 'cmd': 'world_spawn', 'args': {'classname': 'SurvivorM_Mirek'}}] != []`
- `test_starting_run_still_dispatches_mutations` — preservación (OK en el árbol r6)
- `test_credit_during_failed_launch_wait_is_tombstoned_post_rollback_kept` — `AssertionError: 'recent' != 'unknown'`
- `test_basal_does_not_land_at_or_before_tombstone` — `AssertionError: 'stale' != 'unknown'`
- `test_in_flight_writer_does_not_resurrect_stamp_after_unbound_observation` — `AssertionError: 'recent' != 'unknown'`
- `test_probe_cache_is_not_reused_after_revision_bump_during_list_runs` — `AssertionError: 1 not greater than 1`
- `test_exec_enforce_on_idle_run_is_run_not_owned` — `AssertionError: 200 == 200 : {'id': 1, 'peer': 'server', 'cmd': 'exec_enforce'}`

## LO QUE NO PUDE VERIFICAR

- La suite completa fuera de `gate/suite.sh` (prohibido por el brief).
- `tests/test_instance_fence.py` (fuera de la suite acotada y del write-set); no comprobé
  si algún assert de EXITED exige un cerco que P6 no cubre.
- Poll real del bridge más allá del helper `accredited_poll`.
- Que `adopt_run` sobre `run-2` con `run-1` aún activo falle exactamente con
  `active_run_exists` en este fixture: lo inferí del predicado en
  `process_lifecycle.py:2145-2149` y por eso di dueño con `store.replace` en lugar de
  adoptar. No corrí un unittest aparte de esa rama en este fixture.
- El re-sello del orquestador al recibir (él repetirá `run.sh` y `suite.sh`).

## DISPUTAS

Ninguna. El único rojo de la suite era el contrato P6 aplicado a un test anterior; el
producto no se tocó.
