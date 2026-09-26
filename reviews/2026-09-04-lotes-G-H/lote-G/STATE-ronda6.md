## HECHO

La caja se deriva de una foto inmutable y una función pura. `_with_live_activity` desapareció.

- Foto: `_BoxSnapshot` (`clock`, `runs`, `activity`, `unknown`, `revision`) en `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-G\ws\tools\dayz_mcp\process_lifecycle.py:212`.
- Derivador puro: `_derive_box(snapshot, probes)` en `:243`. No lee memoria viva.
- Toma de foto: `_take_box_snapshot` `:2668` — `manifest.list_runs()` y, pegado, `_last_activity` / `_activity_unknown` / revisión bajo `_activity_lock`. Sin E/S entre medias. Sin `_operation_lock`.
- `box_occupancy` `:2651`: «The box is derived from one snapshot; live state is never re-read after it.» Suelta, sondea (`_collect_probes` `:2695`) y deriva. El caché de 1,5 s guarda sólo `foreign` / `ports_in_use` / `scan_known` (`_probes_for_snapshot` `:2753`); las filas no se cachean.
- P3: high-water en `record_box_command_activity` `:1028-1033` — `pop` sólo si el sello es `<=` la observación.
- P5: `_forget_attempt_activity` `:1076` desde `_settle_failed_launch` `:1308-1309`; el intento se marca en `start_run` `:1550`.
- P4, retirada ANTES de la transición durable (grep ` _retire_run_bindings` en `process_lifecycle.py`, cada llamada de producción va inmediatamente antes de `replace`):
  - `stop_run` UNRECONCILED `:1862` → `replace` `:1864`
  - `stop_run` STOPPING `:1930` → `replace` `:1933` (la ventana empieza aquí, no en EXITED)
  - `begin_release_owner` UNRECONCILED `:2207` / `:2235` y EXITED `:2254` → `replace` siguiente
  - `repair_recovery_fault` `:2356` → `replace` `:2358`
  - `repair_manifest_recovery` `:2444` → `restored.replace` `:2446`
  - `_reap_run_locked` `:2517` → `replace` `:2519`
  - `admin_reconcile` EXITED `:2949` → `replace` `:2951`
- Loopback: un binding BOUND cuyo run ya es STOPPING/EXITED/UNRECONCILED (o ausente) no despacha (`_bound_run_has_left_running` `loopback.py:1081`, usado en `_enqueue_fence_target` `:1122` y `:1138`). Lectura tras `retire_run` también es `binding_retired` `:1150-1153`. El oráculo de N17 no asigna `lifecycle.bindings`; sin este fail-closed el enqueue seguiría en 200.

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
          fila={'run_id': 'run-1', 'mod': '@SameMod', 'label': 'gate', 'age_s': 4414371.991, 'owner_session': 'A', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.0} -- las dos fotos coherentes son (state=RUNNING, activity=re
[PASS ] N15-RETROCESO una observacion sin binding mas vieja no borra un sello mas nuevo
          sembrado='recent' despues='recent' -- si cae a 'unknown', una observacion de hace un minuto ha borrado una actividad acreditada posterior, que es exactamente lo que P4 prohibe
[PASS ] N16-SIN-BINDING-MANDA una observacion sin binding posterior si publica unknown
          sembrado='recent' despues='unknown' -- si sigue 'recent', el arreglo de N15 ha desactivado P3 y volvemos a publicar frescura que nadie ha acreditado
[PASS ] N17-BINDING-MUERE-CON-SU-RUN un enqueue tras el EXITED durable se rechaza
          status=409 payload={'error': 'binding_retired', 'hint': 'Target instance was retired (stop, reap, or role replace). Relaunch that role via dayz_test_run so start_run mints a new instance. Resending this command will keep failing until the n
[PASS ] N18-SIN-CACHE-DE-MANIFIESTO la caja publica el estado durable, no el cacheado
          durable='UNRECONCILED' publicado='UNRECONCILED' -- si publica 'RUNNING', las filas salen de una foto vieja y la coherencia depende de haber acertado el incremento del contador en TODAS las mutaciones, incluidas las que aun no se han escrito
========================================================================================================
ORACULO: PASS=29 FAIL=0 UNMET=0 de 29
ORACULO-VERDE
```

```
test_box_occupancy : Ran 70 tests in 1.089s  OK
test_process_lifecycle : Ran 98 tests in 1.175s  OK
test_lifecycle_http : Ran 8 tests in 4.741s  OK
test_loopback : Ran 65 tests in 0.917s  OK
test_mcp_tools : Ran 45 tests in 8.390s  OK
test_session_coordination : Ran 46 tests in 0.084s  OK
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos contra el motor sin el arreglo (12 FAIL, 1 PASS de 13). Línea que demostraba el rojo:

- §2(A) `test_failed_extend_discards_activity_credited_in_the_confirm_window` (`test_process_lifecycle.py:840`): `AssertionError: 'recent' != 'unknown'` — el enqueue en la ventana confirm→replace dejaba `recent` tras el rollback.
- §2(B) `test_stop_run_rejects_enqueue_once_stopping_is_durable` (`:1995`): `AssertionError: 200 == 200` con `state: STOPPING`.
- §2(B) `test_stop_run_rejects_enqueue_once_exited_is_durable` (`:2007`): `AssertionError: 200 == 200` con `state: EXITED`.
- §2(B) `test_begin_release_owner_rejects_enqueue_once_exited_is_durable` (`:2035`): `AssertionError: 200 == 200`.
- §2(B) `test_repair_recovery_fault_rejects_enqueue_once_exited_is_durable` (`:2076`): `AssertionError: 200 == 200`.
- §2(B) `test_repair_manifest_recovery_rejects_enqueue_once_exited_is_durable` (`:2111`): `AssertionError: 200 == 200`.
- §2(B) `test_reap_run_rejects_enqueue_once_exited_is_durable` (`:2126`): `AssertionError: 200 == 200`.
- §2(B) `test_admin_reconcile_rejects_enqueue_once_exited_is_durable` (`:2143`): `AssertionError: 200 == 200`.
- P1 `test_concurrent_occupancy_returns_one_coherent_snapshot` (`test_box_occupancy.py:1144`): `AssertionError: 'unknown' != 'recent'` — collage RUNNING+unknown vía `_with_live_activity`.
- P3 `test_older_unbound_observation_does_not_erase_newer_stamp` (`:1157`): `AssertionError: 'unknown' != 'recent'` — `pop` sin high-water.
- P2 `test_occupancy_rows_are_not_served_from_cache_after_direct_manifest_replace` (`:1180`): `AssertionError: 'RUNNING' != 'UNRECONCILED'`.
- Loopback `test_read_after_retire_run_is_binding_retired` (`test_loopback.py:275`): `AssertionError: 200 == 200`.
- Preservación (verde antes y después): `test_later_unbound_observation_still_publishes_unknown` — P3/N16 ya mandaba; el arreglo de N15 no lo apagó.
- `test_derive_box_is_pure_over_the_snapshot` no formaba parte del rojo-antes: `_derive_box` no existía. ImportError. Cubre que mutar la memoria viva no cambia el resultado de una foto ya tomada.

## LO QUE NO PUDE VERIFICAR

- La ventana de nanosegundos entre `list_runs()` y `_activity_lock` (residuo declarado del ledger): no hay seam de E/S, no la observé.
- `occupied = bool(runs or foreign)` puede mezclar `runs` frescos con un `foreign` de hasta 1,5 s. Fail-closed aguas abajo; no medí un caso donde eso mienta `occupied=False`.
- Suite completa del árbol (~2321 tests): prohibida. Sólo la acotada del gate.
- In-game / DayZDiag real: no se lanzó.
- `test_run_reaper.py` no se tocó; P4 del reap se midió vía `reap_dead_runs` en `test_process_lifecycle.py`.
- N17 del oráculo no asigna `lifecycle.bindings`. La retirada en `stop_run` no alcanza el `ServerState` del check. Lo que el oráculo mide es el fail-closed del enqueue contra el manifiesto durable. Los tests §2(B) sí cablean `bindings` y miden la retirada real.

## DISPUTAS

Ninguna sobre el criterio. N17 es alcanzable; el oráculo no cablea `lifecycle.bindings`, así que el producto necesita las dos capas (retirada en el lifecycle y rechazo en el enqueue si el run ya salió de RUNNING).
