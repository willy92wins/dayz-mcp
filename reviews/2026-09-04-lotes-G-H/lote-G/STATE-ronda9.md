## HECHO

Cerco lógico de `ServerState`: `_fenced_runs` (`loopback.py:856`), activado y con la cola vaciada bajo el mismo lock que enqueue/poll en `fence_runs` (`loopback.py:1131-1142`); revertido en `unfence_runs` (`loopback.py:1144-1148`); levantado por `adopt_run` tras persistir `RUNNING` (`process_lifecycle.py:2248`). Un solo predicado de despacho: `_enqueue_run_rejection` (`loopback.py:1186-1198`) consulta el cerco y el estado durable; `RUNNING_IDLE` rechaza lectura, mutación e `internal=True` con `run_not_owned`. El poll de un binding no despachable (cercado o idle) no entrega y drena (`loopback.py:1172-1174`, `:2044-2055`). Cabecera: `loopback.py:4` y `process_lifecycle.py:3`.

Orden persistir-vs-cercar: `release_owner` (`process_lifecycle.py:2252-2258`) y `begin_release_owner` reconocido (`:2277`) y el extra de la rama de cleanup (`:2375`) llaman a `_quiesce_then_release_owner` (`:1391-1415`): cerca → `manifest.release_owner` → confirma o revierte el cerco. `admin_reconcile` con supervivientes cerca antes de `replace` (`:3070-3076`) y revierte si la persistencia falla.

Cerco de compensación: `with self._compensating(run_id)` (`:1121-1126`, usado en `_settle_failed_launch` `:1434`) marca el run bajo `_activity_lock`; `_seal_activity_locked` (`:1051-1056`) descarta crédito y basal mientras dura; tras el `replace` del rollback corre `_forget_attempt_activity` y el `finally` levanta el cerco en los dos desenlaces (persistencia OK o fallida).

Tests adaptados (F):
- `test_accepted_enqueue_attributes_activity_to_binding_run_among_several` (`test_box_occupancy.py:992`): `run-2` nace con dueño B y `RUNNING` (no `RUNNING_IDLE` sin dueño); se quita el `store.replace` intermedio. Sigue midiendo atribución al binding entre varios.
- `test_inherited_generation_becomes_recent_via_bound_enqueue` (`test_box_occupancy.py:1048`): tras el release se adopta el run heredado (P6 estricto); `run-2` se añade después porque `adopt_run` rechaza con `active_run_exists`. Sigue midiendo unknown → recent vía enqueue ligado, sin acreditar al otro run.

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
          heredado='unknown' enqueue=200 tras_actividad='recent'
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
          fila={'run_id': 'run-1', 'mod': '@SameMod', 'label': 'gate', 'age_s': 4420719.973, 'owner_session': 'A', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.0} -- las dos fotos coherentes son (state=RUNNING, activity=re
[PASS ] N15-RETROCESO una observacion sin binding mas vieja no borra un sello mas nuevo
          sembrado='recent' despues='recent' -- si cae a 'unknown', una observacion de hace un minuto ha borrado una actividad acreditada posterior, que es exactamente lo que P4 prohibe
[PASS ] N16-SIN-BINDING-MANDA una observacion sin binding posterior si publica unknown
          sembrado='recent' despues='unknown' -- si sigue 'recent', el arreglo de N15 ha desactivado P3 y volvemos a publicar frescura que nadie ha acreditado
[PASS ] N17-BINDING-MUERE-CON-SU-RUN un enqueue tras el EXITED durable se rechaza
          status=409 payload={'error': 'binding_retired', 'hint': 'Target instance was retired (stop, reap, or role replace). Relaunch that role via dayz_test_run so start_run mints a new instance. Resending this command will keep failing until the n
[PASS ] N18-SIN-CACHE-DE-MANIFIESTO la caja publica el estado durable, no el cacheado
          durable='UNRECONCILED' publicado='UNRECONCILED' -- si publica 'RUNNING', las filas salen de una foto vieja y la coherencia depende de haber acertado el incremento del contador en TODAS las mutaciones, incluidas las que aun no se han escrito
[PASS ] N19-SIN-DUENO-NO-DESPACHA un run RUNNING_IDLE no acepta comandos
          status=409 payload={'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt it with dayz_test_run mode=client run_id=... before dispatching.'} -- 200 significa que el daemon acepta una mutacion del mundo para un proce
[PASS ] N20-ADOPCION-REHABILITA tras adopt_run el mismo binding vuelve a despachar
          status=200 payload={'id': 1, 'peer': 'server', 'cmd': 'world_spawn'} -- si no es 200, el arreglo de N19 ha retirado el binding en el release y el reattach exige relanzar el servidor
[PASS ] N21-TUMBA un escritor con epoca anterior al borrado no resucita el sello
          estado='unknown' -- 'recent' significa que la escritura acreditada, muestreada ANTES de la observacion sin binding, aterrizo DESPUES y resucito el sello que P3 habia borrado
[PASS ] N22-SELLO-ANTES-DEL-DATO tras una mutacion en vuelo no se reutilizan las sondas
          sondas tras el lector=2, tras la lectura siguiente=3 -- iguales significa que las sondas calculadas contra el manifiesto de antes de la mutacion se guardaron bajo la revision de despues y un lector limpio las reutiliza: filas nuevas con son
[PASS ] N23-LECTURA-SIN-DUENO una lectura sobre un run RUNNING_IDLE se rechaza y no acredita
          status=409 payload={'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt it with dayz_test_run mode=client run_id=... before dispatching.'} eventos_nuevos=0 -- 200 significa que el binding despacha sin dueno; un ev
[PASS ] N25-RELEASE-CERCA-ANTES-DE-PUBLICAR un poll durante el release no se lleva la cola
          poll=200 commands=[] -- un comando entregado significa que el release publico RUNNING_IDLE antes de cercar y vaciar bajo el lock del loopback, y el bridge se llevo una mutacion de un dueno que ya no existe
========================================================================================================
ORACULO: PASS=35 FAIL=0 UNMET=0 de 35
ORACULO-VERDE
```

```
test_box_occupancy : Ran 76 tests in 1.000s   OK 
test_process_lifecycle : Ran 109 tests in 0.902s   OK 
test_lifecycle_http : Ran 8 tests in 4.727s   OK 
test_loopback : Ran 65 tests in 0.927s   OK 
test_mcp_tools : Ran 45 tests in 7.264s   OK 
test_session_coordination : Ran 46 tests in 0.062s   OK 
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Copia temporal `/tmp/r8-control-positivo` desde `../ws-frozen-r8/tools/`, con los dos ficheros de test actuales.

- `test_camera_get_on_idle_run_is_run_not_owned` — `AssertionError: 200 == 200 : {'id': 1, 'peer': 'client', 'cmd': 'camera_get'}`
- `test_internal_command_on_idle_run_is_run_not_owned` — `AssertionError: 200 == 200 : {'id': 1, 'peer': 'client', 'cmd': 'vehicle_release'}`
- `test_credit_during_rollback_replace_is_discarded_post_lift_lands` — `AssertionError: 1788494289.8002653 is not None : {'ok': True, 'stamp': 1788494289.8002653}`
- `test_acknowledged_begin_release_owner_poll_during_persist_is_empty` — `AssertionError: Lists differ: [{'id': 1, 'cmd': 'world_spawn', 'args': {'classname': 'SurvivorM_Mirek'}}] != []`
- `test_admin_reconcile_with_survivors_poll_during_persist_is_empty` — `AssertionError: 200 == 200`
- `test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published` — `AssertionError: Lists differ: [{'id': 1, 'cmd': 'vehicle_release', 'args': {}}] != []`
- `test_failed_rollback_replace_lifts_compensating_fence` — preservación (OK en el árbol r8)
- `test_release_owner_persist_failure_reverts_fence_and_still_dispatches` — preservación (OK en el árbol r8)
- `test_accepted_enqueue_attributes_activity_to_binding_run_among_several` — preservación (OK en el árbol r8 tras dar dueño a `run-2`)
- `test_inherited_generation_becomes_recent_via_bound_enqueue` — preservación (OK en el árbol r8 tras adoptar)
- `test_starting_run_still_dispatches_mutations` — preservación (OK en el árbol r8)

## LO QUE NO PUDE VERIFICAR

- La suite completa fuera de `gate/suite.sh` (prohibido por el brief).
- `tests/test_instance_fence.py` (fuera de la suite acotada; su AST de EXITED no exige el cerco lógico nuevo; no lo toqué).
- Poll real del bridge más allá de `accredited_poll` / `record_poll`.
- Que el oráculo N25, cuyo fixture no asigna `lifecycle.bindings`, ejercite `fence_runs` (el poll fail-closed sobre `RUNNING_IDLE` es lo que lo pone verde cuando el cerco no está cableado). El orden cerca-antes-de-persistir sí se ejercita en los tests de lifecycle con `_bind_run`.
- El re-sello del orquestador al recibir (él repetirá `run.sh` y `suite.sh`).

## DISPUTAS

Ninguna.
