## HECHO

Rebase: copie process_lifecycle.py.r9, test_process_lifecycle.py.r9 y test_box_occupancy.py.r9 sobre los ficheros sin sufijo (base G ronda 9: P6 `_quiesce_then_release_owner` / cerco `_fenced_runs` antes de persistir RUNNING_IDLE, P7 `_compensating`, P8) y reaplique el diff propio de H ronda 1 (campo `daemon_generation_at_launch` al final de RunRecord, anillo en memoria, proyeccion H1, los seis caminos que alimentan el anillo). loopback.py y test_loopback.py no se tocaron. Los tres .r9 ya no existen.

1. El anillo solo afirma lo que el manifiesto confirmo. Unico metodo `_commit_retirement` en tools/dayz_mcp/process_lifecycle.py:1036 (persistir manifest.replace; solo si ok, retirar bindings y `_retire_run_diagnostic` en :1009, que ademas deduplica (run_id, launch_operation_id)). Seis llamadas:
   - stop_run :2294
   - begin_release_owner :2518
   - repair_recovery_fault :2631
   - repair_manifest_recovery :2720
   - _reap_run_locked :2794
   - admin_reconcile :3233
   Un fallo de persistencia no consume posicion: replace hace rollback in-memory y el metodo retorna False.

2. El diagnostico se valida antes del sobre. `_validated_diagnostic` en tools/dayz_mcp/dayz_test_tool.py:834 (ocho claves exactas, run_id exacto, state == EXITED, tipos); `_validated_generation` en :813 para la fila presente-pero-terminal. Unico camino al sobre: execute_dayz_test_stop :890 / :897. `_copy_generation` :851 ya no usa dict.get().

3. repair_recovery_fault rechaza terminales: precondicion `run.state not in _RECOVERY_REPAIR_STATES` en :2581 (EXITED -> identity_ambiguous). Dedup del anillo en `_retire_run_diagnostic` :1017-1033.

4. Lectura rasgada: `_status_snapshot` en :1064 (sello `_box_revision` antes/despues, tope 3, sin `_operation_lock`; filtro final: no publicar diagnostico cuyo run_id tiene fila no terminal en el mismo payload). Usado por status() :2883 y public_status() :2898. La convivencia fila EXITED + diagnostico se conserva.

5. reason cerrado: admin_reconcile pasa reason="admin_reconciled" en :3236; el texto del operador sigue yendo al audit en :3201-3209.

H1-H5 y D1-D4 de ronda 1 siguen en pie (anillo, generaciones, sobre, manifiesto legacy).

## GATE

### bash gate/run.sh

```
========================================================================================================
[PASS ] H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo
          faltan=[] fila={'daemon_generation_at_launch': 'gen-A-a6bb8f97fda44472b181a59404732e61', 'daemon_generation_current': 'gen-A-a6bb8f97fda44472b181a59404732e61', 'generation_changed': False} generacion inyectada='gen-A-a6bb8f97fda44472b181a59404732e61' -- los tres campos se derivan de la generacion que recibe el construc
[PASS ] H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones
          faltan=[] box={'daemon_generation_at_launch': 'gen-A-a6bb8f97fda44472b181a59404732e61', 'daemon_generation_current': 'gen-A-a6bb8f97fda44472b181a59404732e61', 'generation_changed': False} status={'daemon_generation_at_launch': 'gen-A-a6bb8f97fda44472b181a59404732e61', 'daemon_generation_current': 'gen-A-a6bb8f97fda4447
[PASS ] H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva
          faltan=[] fila={'daemon_generation_at_launch': 'gen-1-fb1b8340006048d4af1f5d7d86c31965', 'daemon_generation_current': 'gen-2-a4eaded7543e48daa9857d07a5424539', 'generation_changed': True} lanzamiento='gen-1-fb1b8340006048d4af1f5d7d86c31965' actual='gen-2-a4eaded7543e48daa9857d07a5424539' -- si at_launch sale igual a la
[PASS ] H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false
          faltan=[] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-L2-4dc70134fe0943799e947a825dd8aa63', 'generation_changed': None} campos quitados del manifiesto=['daemon_generation_at_launch'] -- generation_changed=False afirmaria continuidad que nadie acredito; el valor fail-closed es null
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
          error=None runs=['12345678-1234-4234-8234-1234567890ab'] -- un campo nuevo que from_payload no tolere ausente convierte TODO manifiesto preexistente en invalid_run_manifest en el primer arranque tras la entrega
[PASS ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
          tras _clone='gen-D2b-506ff53fe2324ea08c63fbf7a29295cd' tras recargar='gen-D2b-506ff53fe2324ea08c63fbf7a29295cd' esperado='gen-D2b-506ff53fe2324ea08c63fbf7a29295cd' -- _clone hace asdict->from_payload: un campo que from_payload no lea se pierde en silencio en cada get()/list_runs(), sin lanzar nada
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
          launch_operation_id='87654321-4321-4321-8321-ba0987654321' launch_request_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' launch_acknowledged=False -- el campo nuevo va al FINAL con default; insertarlo antes desplaza el unico call-site posicional
[PASS ] H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2a-adc9b04b53e24655af627f79b66fe361', 'daemon_generation_current': 'gen-H2a-adc9b04b53e24655af627f79b66fe361', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped',
[PASS ] H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2b-34c386f3f95e4c39b554460a7c323570', 'daemon_generation_current': 'gen-H2b-34c386f3f95e4c39b554460a7c323570', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 
[PASS ] H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes
          retirados=40 anillo=list publicados=32 (tope 32) faltan_de_los_recientes=[] sobran_viejos=[] -- un anillo sin tope crece con cada retirada y viaja entero en cada status
[PASS ] H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta
          hallazgos=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2d-cebacfd59b2e48a498d9711cc8ead785', 'daemon_generation_current': 'gen-H2d-cebacfd59b2e48a498d9711cc8ead785', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision':
[PASS ] H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia
          anillo tras reiniciar=[] run_presente=False eventos_de_retirada_en_el_jsonl=1 -- publicar un diagnostico aqui significaria haberlo reconstruido leyendo events.jsonl, que es lo que D1 prohibe; la lista vacia es la respuesta correcta
[PASS ] [GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez
          aperturas bajo C:\Users\guill\AppData\Local\Temp\tmph340qxbe\runtime\audit: [] (events.jsonl=2982 bytes; control positivo vio 3 aperturas) -- leer el jsonl desde la ruta de lectura rompe la escritura del writer en Windows; D1 lo prohibe
[PASS ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-661eac99454d457b98d4dafe388ed167', 'daemon_generation_current': 'gen-snapNew-77193065056048de8ebd4b2956c6d506', 'generation_changed': False} -- hoy resolve_sto
[PASS ] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
          snapshot={'daemon_generation_at_launch': 'gen-snapOld-661eac99454d457b98d4dafe388ed167', 'daemon_generation_current': 'gen-snapNew-77193065056048de8ebd4b2956c6d506', 'generation_changed': False} sobre={'daemon_generation_at_launch': 'gen-snapOld-661eac99454d457b98d4dafe388ed167', 'daemon_generation_current': 'gen-snapN
[PASS ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-661eac99454d457b98d4dafe388ed167', 'daemon_generation_current': 'gen-snapNew-77193065056048de8ebd4b2956c6d506', 'generation_changed': False} -- los tres campos 
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
          ok | diagnostico_real={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H3x-221a34334a2149f799c976957129c8a0', 'daemon_generation_current': 'gen-H3x-221a34334a2149f799c976957129c8a0', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 's
[PASS ] H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro
          ok -- hoy DayzTestToolError se traduce a ToolError (server.py:3054-3055) y el sobre no llega nunca al cliente
[PASS ] [GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado
          kind=raise tipo=ToolError texto='Error executing tool dayz_test_stop: run_not_found' campos_inventados=[]
[PASS ] H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero
          claves que arrastran el anillo=[] sobre=['daemon_generation_at_launch', 'daemon_generation_current', 'error_code', 'generation_changed', 'run_id', 'status'] -- el anillo lleva hasta 32 runs ajenos; su sitio es el status publico
[PASS ] H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5a-59ba1ae58839439c97839b99e1879dd0', 'daemon_generation_current': 'gen-H5a-59ba1ae58839439c97839b99e1879dd0', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped', '
[PASS ] H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5b-63da78bb00ad4e4a8880401dccd2e17e', 'daemon_generation_current': 'gen-H5b-63da78bb00ad4e4a8880401dccd2e17e', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 'r
[PASS ] [GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira
          medido=presencia + anillo estado='UNRECONCILED' anillo=list diagnosticos_del_run=[] -- el run sigue presente: publicar una retirada aqui daria por muerto un run que nadie ha conseguido parar
[PASS ] H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre
          fugas=[] (total 0) | control positivo con el diagnostico completo: {'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-661eac99454d457b98d4dafe388ed167', 'daemon_generation_current': 'gen-snapNew-77193065056048de8ebd4b2956c6d5
[PASS ] H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre
          fugas=[] | control positivo: {'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-661eac99454d457b98d4dafe388ed167', 'daemon_generation_current': 'gen-snapNew-77193065056048de8ebd4b2956c6d506', 'generation_changed': False} -- 
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
          olores=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10-86143b9e772d40f48ceed99a9256f088', 'generation_changed': None, 'event': 'admin_reconcile', 'reason': 'admin_reconciled', 'decision': 'confirmed', 'state': 'EXITED'} (contro
[PASS ] H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10b-899e055ab82143299c5c501b5a308db3', 'generation_changed': None, 'event': 'admin_reconcile', 'reason': 'admin_reconciled', 'decision': 'confirmed', 'state': 'EXITED'} reason_es_codigo
========================================================================================================
ORACULO-CENSO OK: 40 checks, ninguno repetido
ORACULO: PASS=40 FAIL=0 UNMET=0 de 40
ORACULO-VERDE
```

### bash gate/suite.sh

```
test_process_lifecycle : Ran 150 tests in 2.380s  OK
test_box_occupancy : Ran 77 tests in 1.033s  OK
test_lifecycle_http : Ran 8 tests in 4.725s  OK
test_run_reaper : Ran 7 tests in 0.045s  OK
test_bug104_reap_under_quarantine : Ran 10 tests in 0.080s  OK
test_task7_final_lifecycle_regressions : Ran 8 tests in 0.060s  OK
test_dayz_test_tool : Ran 52 tests in 0.234s  OK
test_dayz_test_value_error_codes : Ran 8 tests in 0.049s  OK
test_mcp_tools : Ran 45 tests in 7.276s  OK
test_effective_schema_catalog : Ran 5 tests in 0.001s  OK
test_loopback : Ran 65 tests in 0.939s  OK
SUITE-ACOTADA OK
```

### oracle_lote_g.py

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
          fila={'run_id': 'run-1', 'mod': '@SameMod', 'label': 'gate', 'age_s': 4422623.381, 'owner_session': 'A', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.0, 'daemon_generation_at_launch': None, 'daemon_generation_cur
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

## CONTROL POSITIVO

Copia temporal de ../ws-frozen-r1/tools/ bajo %TEMP%/lote-h-r2-frozen/tools, con los tests nuevos superpuestos. unittest -v:

(A) Fallo de persistencia, seis caminos
- test_a_persist_failure_on_reap_publishes_nothing_and_keeps_the_run ... FAIL
- test_a_persist_failure_on_stop_publishes_nothing_and_keeps_the_run ... ok — preservacion (stop_run ya persistia antes de publicar en ronda 1)
- test_a_persist_failure_on_repair_recovery_publishes_nothing ... FAIL
- test_a_persist_failure_on_repair_manifest_recovery_publishes_nothing ... FAIL
- test_a_persist_failure_on_begin_release_owner_publishes_nothing ... FAIL
- test_a_persist_failure_on_admin_reconcile_publishes_nothing ... FAIL
- test_a_order_ring_still_empty_at_the_replace_of_a_healthy_reap ... FAIL

(B) 33 pasadas con replace siempre fallando
- test_b_failed_reaps_do_not_evict_a_prior_legitimate_diagnostic ... FAIL

(C) repair_recovery_fault repetido
- test_c_repeated_repair_recovery_fault_is_rejected_with_one_diagnostic ... FAIL

(D) Lectura rasgada y preservacion EXITED
- test_d_torn_status_never_pairs_a_live_row_with_its_diagnostic ... FAIL
- test_d_torn_public_status_never_pairs_a_live_row_with_its_diagnostic ... FAIL
- test_d_stop_then_status_without_reload_keeps_exited_row_and_diagnostic ... ok — preservacion (la ficha exige fila EXITED + diagnostico tras stop)

(E) reason cerrado
- test_e_admin_reconcile_publishes_closed_reason_and_keeps_operator_text_in_audit ... FAIL

(F) Rebase
- preservacion: oraculo G 35/35 ORACULO-VERDE; suite acotada incluye los tests de ronda 9 (test_process_lifecycle 150, test_loopback 65, test_box_occupancy 77) en OK. loopback.py no se toco.

Sobre (H6), tests nuevos contra frozen-r1:
- test_incomplete_diagnostic_raises_run_not_found_without_fabricating_nulls ... FAIL (subtests missing event/reason/decision/state/generaciones)
- test_diagnostic_with_non_exited_state_raises_run_not_found ... FAIL
- test_present_inactive_row_without_generation_raises_run_not_active ... FAIL

Tests de ronda 1 reejecutados contra frozen-r1 (anillo, generaciones, seis alimentadores, tope 32): todos ok — preservacion.

## LO QUE NO PUDE VERIFICAR

- Suite completa no acotada (el brief la prohibe). Solo los once modulos de gate/suite.sh.
- Daemon real ni proceso DayZDiag; todo el ejercicio es fixture in-process.
- Carrera con hilos reales de reap vs status mas alla de la barrera determinista sobre manifest.list_runs (H9/oraculo).
- PermissionError de os.replace contra un handle abierto de events.jsonl en Windows (D1, medido en la sesion redactora; aqui no se reabre el jsonl).
- Apply mecanico `diff r8 mi-entrega | patch` sobre el .r9: el diff r8->r1 chocaba con hunks P6/P7/P8. El rebase se hizo copiando .r9 y reaplicando el diff propio de H.
- test_instance_fence no esta en la suite acotada; la retirada de bindings tras persistir se cubre por _commit_retirement y por los tests de lifecycle, no por un InstanceFence vivo.

## DISPUTAS

Ninguna.
