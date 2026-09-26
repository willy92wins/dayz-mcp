## HECHO

Cabecera (ambos ficheros): «Every transition into RUNNING_IDLE goes through the fence; the poll re-validates after re-acquiring the lock.» — `tools/dayz_mcp/process_lifecycle.py:3-4`, `tools/dayz_mcp/loopback.py:4-5`.

Método único: `_transition_to_idle(runs, persist)` en `tools/dayz_mcp/process_lifecycle.py:1538-1559` (fence+drain bajo el lock del loopback → persistir → confirmar; `unfence` si la persistencia falla o el run no quedó `RUNNING_IDLE`).

Callers de `_transition_to_idle`:
- `ProcessLifecycle._quiesce_then_release_owner` `:1574` → `manifest.release_owner`. A su vez: `release_owner` `:2420`, `begin_release_owner` `:2457` y `:2565`.
- `ProcessLifecycle.release_all_running_owners` `:2433` → `manifest.release_all_running_owners`. No había caller productivo del store; el wrapper del lifecycle es ahora el único camino cercado. Documentado.
- `repair_manifest_recovery` `:2766` → `restored.recover_after_restart`.
- `admin_reconcile` con supervivientes `:3288` → `manifest.replace` (la asignación local a `RUNNING_IDLE` está en `:3277`; la persistencia pasa por el método único).

Grep de `run.state = "RUNNING_IDLE"` en los dos ficheros del write-set: solo los tres productores del store (`release_owner` `:842`, `release_all_running_owners` `:865`, `recover_after_restart` `:889`) y la escritura local de `admin_reconcile` `:3277`. Ninguno es un caller productivo suelto: los tres del store son el `persist` de `_transition_to_idle`; reconcile también. `loopback.py` no asigna `RUNNING_IDLE` (solo lo nombra en el hint `:100` y en `_enqueue_run_rejection` `:1204`).

Revalidación del poll: `record_poll` retoma el lock en `tools/dayz_mcp/loopback.py:2124` y, antes de comprometer la entrega, evalúa `_poll_should_hold_commands` `:2128-2134` (cerco + estado durable). Si hay que retener, drena y entrega `commands=[]`.

Lector durante compensación: `_activity_for_run_id` consulta `_compensating_runs` en `:1234`. La derivación de la caja: `_BoxSnapshot.compensating` `:224`, copiado en `_take_box_snapshot` `:3010-3012`, leído por `_activity_from_snapshot` `:237`. Un sello del intento no se publica mientras dura la ventana; la tumba sigue siendo la frontera del rollback.

Baja del cerco en retire: `ServerState.retire_run` `tools/dayz_mcp/loopback.py:1016-1024` hace `_fenced_runs.discard(run_id)` dentro del mismo `_lock`, aunque no queden entradas en `_role_index`.

Código de `exec_enforce`: la segunda validación conserva `fence_error_code` (`:1792`, `:1795-1803`) y, si se perdió el cerco, responde con `_fence_reject_response` `:1840-1842`. `enqueue_cancelled` queda para stop/retirada (`_stopping` / generación, `fence_error_code is None`).

## GATE

### gate/run.sh (oráculo H)

```
========================================================================================================
[PASS ] H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo
          faltan=[] fila={'daemon_generation_at_launch': 'gen-A-04b943f5c0024f86a574d4a6a45e6a99', 'daemon_generation_current': 'gen-A-04b943f5c0024f86a574d4a6a45e6a99', 'generation_changed': False} generacion inyectada='gen-A-04b943f5c0024f86a574d4a6a45e6a99' -- los tres campos se derivan de la generacion que recibe el construc
[PASS ] H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones
          faltan=[] box={'daemon_generation_at_launch': 'gen-A-04b943f5c0024f86a574d4a6a45e6a99', 'daemon_generation_current': 'gen-A-04b943f5c0024f86a574d4a6a45e6a99', 'generation_changed': False} status={'daemon_generation_at_launch': 'gen-A-04b943f5c0024f86a574d4a6a45e6a99', 'daemon_generation_current': 'gen-A-04b943f5c0024f8
[PASS ] H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva
          faltan=[] fila={'daemon_generation_at_launch': 'gen-1-5e7d3688dd894f7c990bf7855bde738d', 'daemon_generation_current': 'gen-2-3fdbda2956df4d03a1331290ae05f9d6', 'generation_changed': True} lanzamiento='gen-1-5e7d3688dd894f7c990bf7855bde738d' actual='gen-2-3fdbda2956df4d03a1331290ae05f9d6' -- si at_launch sale igual a la
[PASS ] H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false
          faltan=[] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-L2-5ad8098ac29e413892d4a3e9bbbdd0eb', 'generation_changed': None} campos quitados del manifiesto=['daemon_generation_at_launch'] -- generation_changed=False afirmaria continuidad que nadie acredito; el valor fail-closed es null
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
          error=None runs=['12345678-1234-4234-8234-1234567890ab'] -- un campo nuevo que from_payload no tolere ausente convierte TODO manifiesto preexistente en invalid_run_manifest en el primer arranque tras la entrega
[PASS ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
          tras _clone='gen-D2b-8159aeab6cc145f3bb7a2191df8ea74c' tras recargar='gen-D2b-8159aeab6cc145f3bb7a2191df8ea74c' esperado='gen-D2b-8159aeab6cc145f3bb7a2191df8ea74c' -- _clone hace asdict->from_payload: un campo que from_payload no lea se pierde en silencio en cada get()/list_runs(), sin lanzar nada
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
          launch_operation_id='87654321-4321-4321-8321-ba0987654321' launch_request_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' launch_acknowledged=False -- el campo nuevo va al FINAL con default; insertarlo antes desplaza el unico call-site posicional
[PASS ] H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2a-9e22cc55542d42068ebb5d756fbac990', 'daemon_generation_current': 'gen-H2a-9e22cc55542d42068ebb5d756fbac990', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped',
[PASS ] H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2b-a35b1e91f6034eb9a26082b07af13213', 'daemon_generation_current': 'gen-H2b-a35b1e91f6034eb9a26082b07af13213', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 
[PASS ] H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes
          retirados=40 anillo=list publicados=32 (tope 32) faltan_de_los_recientes=[] sobran_viejos=[] -- un anillo sin tope crece con cada retirada y viaja entero en cada status
[PASS ] H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta
          hallazgos=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2d-7526f42c27f84849ba572a24dbd861ea', 'daemon_generation_current': 'gen-H2d-7526f42c27f84849ba572a24dbd861ea', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision':
[PASS ] H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia
          anillo tras reiniciar=[] run_presente=False eventos_de_retirada_en_el_jsonl=1 -- publicar un diagnostico aqui significaria haberlo reconstruido leyendo events.jsonl, que es lo que D1 prohibe; la lista vacia es la respuesta correcta
[PASS ] [GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez
          aperturas bajo C:\Users\guill\AppData\Local\Temp\tmpgob78shq\runtime\audit: [] (events.jsonl=2983 bytes; control positivo vio 3 aperturas) -- leer el jsonl desde la ruta de lectura rompe la escritura del writer en Windows; D1 lo prohibe
[PASS ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-f41a6baaab664e3abc983689a5b5bfc9', 'daemon_generation_current': 'gen-snapNew-96a80a3c1dcf437ebf78e76557707c1d', 'generation_changed': False} -- hoy resolve_sto
[PASS ] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
          snapshot={'daemon_generation_at_launch': 'gen-snapOld-f41a6baaab664e3abc983689a5b5bfc9', 'daemon_generation_current': 'gen-snapNew-96a80a3c1dcf437ebf78e76557707c1d', 'generation_changed': False} sobre={'daemon_generation_at_launch': 'gen-snapOld-f41a6baaab664e3abc983689a5b5bfc9', 'daemon_generation_current': 'gen-snapN
[PASS ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-f41a6baaab664e3abc983689a5b5bfc9', 'daemon_generation_current': 'gen-snapNew-96a80a3c1dcf437ebf78e76557707c1d', 'generation_changed': False} -- los tres campos 
[PASS ] [GUARDA] H3-MUT-PODADO-NO-ES-NOT-ACTIVE un run ausente nunca se publica como run_not_active
          codigo='run_not_found' (kind=dict) -- run_not_active afirma presencia; el run no esta en runs y solo lo acredita un diagnostico retirado
[PASS ] [GUARDA] H3-FAIL-CLOSED-UUID-NUNCA-VISTO sin evidencia se levanta run_not_found pelado
          kind=raise tipo=DayzTestToolError codigo='run_not_found' campos_inventados=[] -- un sobre aqui seria historia inventada para un UUID del que no hay ninguna evidencia
[PASS ] [GUARDA] H3-FAIL-CLOSED-DIAG-AMBIGUO dos diagnosticos del mismo run no acreditan nada
          kind=raise devuelto=DayzTestToolError('run_not_found') -- con dos diagnosticos contradictorios no hay uno exacto: elegir cualquiera de ellos es inventar cual fue la retirada real
[PASS ] [GUARDA] H3-UN-SOLO-SNAPSHOT-PRE-DISPATCH exactamente una lectura antes de despachar
          lecturas de lifecycle_status antes del despacho=1 (total=2) -- dos fotos pre-dispatch permiten clasificar con una y ejecutar con otra
[PASS ] [GUARDA] H3-STOP-ACTIVO-SIGUE-IGUAL el camino feliz no cambia
          devuelto={'status': 'succeeded', 'project': 'StorageMod', 'mode': 'stop', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'phase': 'completed', 'elapsed_s': 0.0, 'artifacts_paths': ['C:\\Tools\\LFV_D2_Executor\\_client\\profiles'], 'error_code': None, 'cleanup_degraded': False, 'server_alive': None, 'client_alive': N
[PASS ] [GUARDA] H3-RELECTURA-POST-EJECUCION-INTACTA run_stop_failed se sigue reconciliando
          status='succeeded' error_code=None lecturas=2 -- fusionar esa relectura con el snapshot pre-dispatch dejaria un stop exitoso publicado como fallido
[PASS ] H3-INTEGRACION-STATUS-REAL el status del lifecycle alimenta el sobre
          ok | diagnostico_real={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H3x-0e4f57cee29b4067b1dfdf482ab86ce6', 'daemon_generation_current': 'gen-H3x-0e4f57cee29b4067b1dfdf482ab86ce6', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 's
[PASS ] H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro
          ok -- hoy DayzTestToolError se traduce a ToolError (server.py:3054-3055) y el sobre no llega nunca al cliente
[PASS ] [GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado
          kind=raise tipo=ToolError texto='Error executing tool dayz_test_stop: run_not_found' campos_inventados=[]
[PASS ] H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero
          claves que arrastran el anillo=[] sobre=['daemon_generation_at_launch', 'daemon_generation_current', 'error_code', 'generation_changed', 'run_id', 'status'] -- el anillo lleva hasta 32 runs ajenos; su sitio es el status publico
[PASS ] H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5a-776100bae5ef4c8e8d353a2bedc82b5b', 'daemon_generation_current': 'gen-H5a-776100bae5ef4c8e8d353a2bedc82b5b', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped', '
[PASS ] H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5b-f2c6c2d31f0246eaa409e6044f694129', 'daemon_generation_current': 'gen-H5b-f2c6c2d31f0246eaa409e6044f694129', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 'r
[PASS ] [GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira
          medido=presencia + anillo estado='UNRECONCILED' anillo=list diagnosticos_del_run=[] -- el run sigue presente: publicar una retirada aqui daria por muerto un run que nadie ha conseguido parar
[PASS ] H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre
          fugas=[] (total 0) | control positivo con el diagnostico completo: {'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-f41a6baaab664e3abc983689a5b5bfc9', 'daemon_generation_current': 'gen-snapNew-96a80a3c1dcf437ebf78e76557707c
[PASS ] H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre
          fugas=[] | control positivo: {'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-f41a6baaab664e3abc983689a5b5bfc9', 'daemon_generation_current': 'gen-snapNew-96a80a3c1dcf437ebf78e76557707c1d', 'generation_changed': False} -- 
[PASS ] H7-FALLO-DE-PERSISTENCIA-NO-PUBLICA un replace roto no consume una posicion
          caminos con fuga=[] | sanos={'reap': (1, 1), 'repair_recovery_fault': (1, 1), 'admin_reconcile': (1, 1), 'stop_run': (1, 2)} -- publicar antes de persistir acredita una retirada que el disco nunca acepto: el run sigue vivo en el manifiesto Y la entrada fantasma desaloja historia real de un anillo de 32 posiciones
[PASS ] H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR al persistir, el anillo aun no conoce el run
          caminos que publican ANTES de persistir=[] | por camino={'reap': [False], 'repair_recovery_fault': [False], 'admin_reconcile': [False], 'stop_run': [False, False]} -- stop_run ya lo hace bien: persiste y luego publica. Es la misma invariante para los seis caminos que retiran runs
[PASS ] H7-PERSISTENCIA-OK-SI-PUBLICA una retirada sana sigue dejando su diagnostico
          diagnosticos por camino={'reap': 1, 'repair_recovery_fault': 1, 'admin_reconcile': 1, 'stop_run': 1} estado final={'reap': 'EXITED', 'repair_recovery_fault': 'EXITED', 'admin_reconcile': 'EXITED', 'stop_run': 'EXITED'} -- si esto se pone rojo, la correccion de H7 ha borrado el producto en vez de ordenarlo
[PASS ] H7-ANILLO-NO-SE-CONSUME-CON-FALLOS 33 retiradas fallidas no desalojan historia
          pasadas=33 desalojados=[] de ['real-00', 'real-01', 'real-02'] entradas_del_objetivo=0 estado='RUNNING' -- el reaper corre cada 30 s: con una entrada por intento fallido, en poco mas de 15 min el anillo de 32 posiciones es entero el MISMO run que nunca se retiro, y la historia real esta desalojada
[PASS ] H8-REPARACION-REPETIDA-RECHAZADA reparar un run ya EXITED no anade otro diagnostico
          primera=True segunda={'terminal_safe': False, 'error': 'identity_ambiguous'} diagnosticos 1a->2a: 1->1 estado tras la 1a='EXITED' -- el segundo diagnostico duplica la cardinalidad y tumba el sobre de dayz_test_stop a ToolError: un run perfectamente diagnosticable deja de serlo
[PASS ] H9-STATUS-SIN-LECTURA-RASGADA ninguna fila no terminal convive con su diagnostico
          hallazgos=[] -- una fila RUNNING junto a un diagnostico EXITED del mismo run solo puede salir de leer el manifiesto antes de la retirada y el anillo despues; el lector no puede saber cual de las dos mitades es la vieja
[PASS ] [GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA la fila EXITED y su diagnostico conviven
          fila='EXITED' diagnosticos=1 -- sin recargar, status() publica los EXITED: la ficha 16 exige que el run presente-pero-terminal siga en runs Y que status conserve su diagnostico. Esto NO es la lectura rasgada de H9.
[PASS ] H10-ADMIN-REASON-NO-VIAJA el texto libre del operador no llega al diagnostico
          olores=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10-1c55402a86994f568236f982deaf7bfa', 'generation_changed': None, 'event': 'admin_reconcile', 'reason': 'admin_reconciled', 'decision': 'confirmed', 'state': 'EXITED'} (contro
[PASS ] H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10b-cf2bb83e4dce4ba98b1c2e4d65e0e359', 'generation_changed': None, 'event': 'admin_reconcile', 'reason': 'admin_reconciled', 'decision': 'confirmed', 'state': 'EXITED'} reason_es_codigo
========================================================================================================
ORACULO-CENSO OK: 40 checks, ninguno repetido
ORACULO: PASS=40 FAIL=0 UNMET=0 de 40
ORACULO-VERDE
```

### gate-extra/oracle_lote_g.py (oráculo G)

```
========================================================================================================
[PASS ] G1-899s activity_state es 'recent'
          valor='recent' fila=['activity_state', 'age_s', 'daemon_generation_at_launch', 'daemon_generation_current', 'generation_changed', 'label', 'last_activity_age_s', 'mod', 'owner_session', 'run_id', 'state']
[PASS ] G1-901s activity_state es 'stale'
          valor='stale' fila=['activity_state', 'age_s', 'daemon_generation_at_launch', 'daemon_generation_current', 'generation_changed', 'label', 'last_activity_age_s', 'mod', 'owner_session', 'run_id', 'state']
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
          fila={'run_id': 'run-1', 'mod': '@SameMod', 'label': 'gate', 'age_s': 4423786.21, 'owner_session': 'A', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.0, 'daemon_generation_at_launch': None, 'daemon_generation_curr
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
[PASS ] N26-POLL-REVALIDA una transicion a RUNNING_IDLE en la ventana del poll no entrega
          poll=200 commands=[] -- un comando entregado significa que el poll copio la cola con dueno y la entrego sin dueno: la transicion de la reparacion no cerco, y el poll no revalido al retomar el lock
[PASS ] N28-CERCO-NO-SE-ACUMULA retirar un run borra su cerco
          _fenced_runs tras el reap=[] -- una marca logica que sobrevive a la retirada fisica de su run se acumula hasta el reinicio
[PASS ] N29-EXEC-ENFORCE-RUN-NOT-OWNED perder la carrera con un release devuelve run_not_owned
          status=409 error='run_not_owned' -- 200 seria despacho sin dueno; 'enqueue_cancelled' es fail-closed pero colapsa el motivo que P6 exige
========================================================================================================
ORACULO: PASS=38 FAIL=0 UNMET=0 de 38
ORACULO-VERDE
```

### gate/suite.sh

```
test_process_lifecycle : Ran 155 tests in 2.272s
 
 OK
 
test_box_occupancy : Ran 79 tests in 1.086s
 
 OK
 
test_lifecycle_http : Ran 8 tests in 4.721s
 
 OK
 
test_run_reaper : Ran 7 tests in 0.037s
 
 OK
 
test_bug104_reap_under_quarantine : Ran 10 tests in 0.081s
 
 OK
 
test_task7_final_lifecycle_regressions : Ran 8 tests in 0.052s
 
 OK
 
test_dayz_test_tool : Ran 52 tests in 0.233s
 
 OK
 
test_dayz_test_value_error_codes : Ran 8 tests in 0.050s
 
 OK
 
test_mcp_tools : Ran 45 tests in 7.256s
 
 OK
 
test_effective_schema_catalog : Ran 5 tests in 0.001s
 
 OK
 
test_loopback : Ran 67 tests in 1.044s
 
 OK
 
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos ANTES del cambio contra `lote-H/ws-frozen-r2/tools/` (árbol fusionado G r9 + H r2; el brief §5 dice `ws-frozen-r9`, el §4 apunta a esta copia). Copia temporal en `/tmp/r10-frozen-EDaEFR`.

(A) `test_poll_revalidates_after_lock_when_store_releases_without_fence`:
```
AssertionError: Lists differ: [{'id': 1, 'cmd': 'world_spawn', 'args': {'classname': 'SurvivorM_Mirek'}}] != []
```
(A) `test_poll_in_repair_window_does_not_deliver` (tras leer `lifecycle.manifest`):
```
AssertionError: Lists differ: [{'id': 1, 'cmd': 'world_spawn', 'args': {'classname': 'SurvivorM_Mirek'}}] != []
```

(B) `test_release_all_running_owners_poll_during_persist_is_empty`:
```
AttributeError: 'ProcessLifecycle' object has no attribute 'release_all_running_owners'
AssertionError: False is not true
```
(el hilo del wrapper no existía; `dentro.wait` no armó. Rojo-antes del productor sin caller.)
(B) `test_admin_reconcile_with_survivors_poll_during_persist_is_empty` — preservación (ya cercaba en la 9; OK en frozen).

(C) `test_reader_during_rollback_replace_publishes_unknown`:
```
AssertionError: 'recent' != 'unknown'
```

N28 `test_release_then_reap_drops_fence`:
```
AssertionError: 'run-existing' unexpectedly found in {'run-existing'}
```
N28 `test_retire_run_discards_fence_even_without_bindings`:
```
AssertionError: 'orphan-run' unexpectedly found in {'orphan-run'}
```
N29 `test_exec_enforce_losing_race_with_release_is_run_not_owned`:
```
AssertionError: 'enqueue_cancelled' != 'run_not_owned'
```
`test_compensating_snapshot_publishes_unknown`:
```
TypeError: _BoxSnapshot.__init__() got an unexpected keyword argument 'compensating'
```

(D) Preservación (OK en el árbol ya cambiado; N20/N25 verdes en el oráculo G): `test_release_owner_drains_pending_queue_and_adopt_rehabilitates`, `test_acknowledged_begin_release_owner_poll_during_persist_is_empty`, `test_release_owner_persist_failure_reverts_fence_and_still_dispatches`, tests de la 9 de compensación (`test_credit_during_failed_launch_wait_is_tombstoned_post_rollback_kept`, `test_credit_during_rollback_replace_is_discarded_post_lift_lands`, `test_failed_rollback_replace_lifts_compensating_fence`).

## LO QUE NO PUDE VERIFICAR

- No corrí la suite completa del árbol (el brief lo prohíbe). Solo `gate/suite.sh` (11 módulos) y los unittest nuevos.
- No hay proceso DayZ ni bridge real: N26/N28/N29 y (A)-(C) son fixtures con barreras en `command_requires_lease`, `exec_audit`, `replace` y el store.
- `RunManifestStore.release_owner` / `release_all_running_owners` / `recover_after_restart` siguen asignando `RUNNING_IDLE` por dentro (`:842`, `:865`, `:889`). Un caller que invoque el store a pelo (tests del oráculo N25 parchean `manifest.release_owner` como `persist`, no como caller productivo) no pasa por el cerco. No instrumenté un grep de callers fuera del write-set en runtime.
- No volví a hashear `gate-extra/oracle_lote_g.py` contra el sello `4c562895b057a970…`; lo corrí como oráculo sellado de recepción.
- El leftover de `_transition_to_idle` (unfence si el persist no dejó `RUNNING_IDLE`) no tiene un test propio de subconjunto: solo el camino feliz y el fallo de persistencia de release (preservación).
- `test_release_all_running_owners_poll_during_persist_is_empty` en frozen no midió la entrega del poll: el wrapper no existía y el wait expiró. El rojo-antes es de API, no de commands=[].
- No verifiqué un `box_occupancy` HTTP /status concurrente; solo el lector in-process del fixture adoptar-y-extender.

## DISPUTAS

Ninguna. N28 lee `_fenced_runs` (caja blanca declarada); el atributo no se renombró.
