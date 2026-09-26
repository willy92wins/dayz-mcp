## HECHO

P22'. `_enqueue_run_rejection` (`tools/dayz_mcp/loopback.py:1271-1286`) ya no trata `lifecycle is None` como permiso: un `run_id` acreditado consulta `_durable_run_state` y, sin prueba durable, devuelve `run_state_unavailable` (503). `_poll_should_hold_commands` (`:1257-1262`) retiene cuando hay `run_id` y el run no es despachable; la cola legacy sin `run_id` sigue entregando. `exec_enforce` entra por el mismo `_enqueue_fence_target`. `install_bound_peer` (`:1068-1104`) es el helper de tests: si no hay lifecycle, adjunta `_BoundPeerDispatchable` (`:827-846`) para que los tests que no cablean ProcessLifecycle sigan despachando; N38/P22' ponen `lifecycle = None` después del install. Producción no llama ese helper.

P20'. `_write_command_activity` (`tools/dayz_mcp/process_lifecycle.py:1324-1342`): la rama `not ok` compara `epoch` con `_activity_tombstone` antes de añadir sticky; `epoch ≤ tumba` se descarta sin recrear `_activity_unknown`. Un fallo con `epoch > tumba` sigue siendo sticky.

P19'. Decisión: **rechazar**, no reconstruir. `adopt_run` (`:2481-2509`) consulta `_bindings_retired_pending_reap` (`:1023-1033`) → `ServerState.run_bindings_retired` (`loopback.py:1106-1111`, poblado en `retire_run` `:1064`). Si el binding se retiró y el persist terminal no aterrizó, responde `binding_retired_pending_reap` (409) con hint a reap/recovery (`_BINDING_RETIRED_PENDING_REAP_HINT` `:281-284`). No restaura el binding a ciegas. El reap siguiente converge (el `replace` EXITED ya no está inyectado). `release_owner` no retira el binding: N20 sigue rehabilitando.

## GATE

### gate/run.sh (oráculo H)

```
========================================================================================================
[PASS ] H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo
          faltan=[] fila={'daemon_generation_at_launch': 'gen-A-a5161286f1794efc90e146267773bdef', 'daemon_generation_current': 'gen-A-a5161286f1794efc90e146267773bdef', 'generation_changed': False} generacion inyectada='gen-A-a5161286f1794efc90e146267773bdef' -- los tres campos se derivan de la generacion que recibe el construc
[PASS ] H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones
          faltan=[] box={'daemon_generation_at_launch': 'gen-A-a5161286f1794efc90e146267773bdef', 'daemon_generation_current': 'gen-A-a5161286f1794efc90e146267773bdef', 'generation_changed': False} status={'daemon_generation_at_launch': 'gen-A-a5161286f1794efc90e146267773bdef', 'daemon_generation_current': 'gen-A-a5161286f1794ef
[PASS ] H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva
          faltan=[] fila={'daemon_generation_at_launch': 'gen-1-9c39bc62f70d4138ace18964442dc031', 'daemon_generation_current': 'gen-2-63fdf62d53b441329ddc7512bd198e7a', 'generation_changed': True} lanzamiento='gen-1-9c39bc62f70d4138ace18964442dc031' actual='gen-2-63fdf62d53b441329ddc7512bd198e7a' -- si at_launch sale igual a la
[PASS ] H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false
          faltan=[] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-L2-50ab19b0a13d4120829ada00c5c435ab', 'generation_changed': None} campos quitados del manifiesto=['daemon_generation_at_launch'] -- generation_changed=False afirmaria continuidad que nadie acredito; el valor fail-closed es null
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
          error=None runs=['12345678-1234-4234-8234-1234567890ab'] -- un campo nuevo que from_payload no tolere ausente convierte TODO manifiesto preexistente en invalid_run_manifest en el primer arranque tras la entrega
[PASS ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
          tras _clone='gen-D2b-60c3157f95b44a9f94067b37076eb2f3' tras recargar='gen-D2b-60c3157f95b44a9f94067b37076eb2f3' esperado='gen-D2b-60c3157f95b44a9f94067b37076eb2f3' -- _clone hace asdict->from_payload: un campo que from_payload no lea se pierde en silencio en cada get()/list_runs(), sin lanzar nada
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
          launch_operation_id='87654321-4321-4321-8321-ba0987654321' launch_request_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' launch_acknowledged=False -- el campo nuevo va al FINAL con default; insertarlo antes desplaza el unico call-site posicional
[PASS ] H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2a-cc12222be0f84babb23040da3927acae', 'daemon_generation_current': 'gen-H2a-cc12222be0f84babb23040da3927acae', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped',
[PASS ] H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2b-31c56b2a22494f65bc7d137214e313ce', 'daemon_generation_current': 'gen-H2b-31c56b2a22494f65bc7d137214e313ce', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision':
[PASS ] H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes
          retirados=40 anillo=list publicados=32 (tope 32) faltan_de_los_recientes=[] sobran_viejos=[] -- un anillo sin tope crece con cada retirada y viaja entero en cada status
[PASS ] H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta
          hallazgos=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2d-1a13633579d244fa9c9efb16560f4cfc', 'daemon_generation_current': 'gen-H2d-1a13633579d244fa9c9efb16560f4cfc', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision':
[PASS ] H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia
          anillo tras reiniciar=[] run_presente=False eventos_de_retirada_en_el_jsonl=1 -- publicar un diagnostico aqui significaria haberlo reconstruido leyendo events.jsonl, que es lo que D1 prohibe; la lista vacia es la respuesta correcta
[PASS ] [GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez
          aperturas bajo C:\Users\guill\AppData\Local\Temp\tmpjm0w_yfz\runtime\audit: [] (events.jsonl=2982 bytes; control positivo vio 3 aperturas) -- leer el jsonl desde la ruta de lectura rompe la escritura del writer en Windows; D1 lo prohibe
[PASS ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-554e6445c41b4e2b8ac11a824c85f47a', 'daemon_generation_current': 'gen-snapNew-fb3bbd673f29499da7881b512d9f8129', 'generation_changed': False} -- hoy resolve_sto
[PASS ] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
          snapshot={'daemon_generation_at_launch': 'gen-snapOld-554e6445c41b4e2b8ac11a824c85f47a', 'daemon_generation_current': 'gen-snapNew-fb3bbd673f29499da7881b512d9f8129', 'generation_changed': False} sobre={'daemon_generation_at_launch': 'gen-snapOld-554e6445c41b4e2b8ac11a824c85f47a', 'daemon_generation_current': 'gen-snapN
[PASS ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-554e6445c41b4e2b8ac11a824c85f47a', 'daemon_generation_current': 'gen-snapNew-fb3bbd673f29499da7881b512d9f8129', 'generation_changed': False} -- los tres campos
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
          ok | diagnostico_real={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H3x-049ccfae28894b0b9a82173e861f05fd', 'daemon_generation_current': 'gen-H3x-049ccfae28894b0b9a82173e861f05fd', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 's
[PASS ] H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro
          ok -- hoy DayzTestToolError se traduce a ToolError (server.py:3054-3055) y el sobre no llega nunca al cliente
[PASS ] [GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado
          kind=raise tipo=ToolError texto='Error executing tool dayz_test_stop: run_not_found' campos_inventados=[]
[PASS ] H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero
          claves que arrastran el anillo=[] sobre=['daemon_generation_at_launch', 'daemon_generation_current', 'error_code', 'generation_changed', 'run_id', 'status'] -- el anillo lleva hasta 32 runs ajenos; su sitio es el status publico
[PASS ] H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5a-d98c2990a02c4b8895c322d684ff57ba', 'daemon_generation_current': 'gen-H5a-d98c2990a02c4b8895c322d684ff57ba', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped', '
[PASS ] H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5b-5a01a1576bde40f8b574dd2d639f9267', 'daemon_generation_current': 'gen-H5b-5a01a1576bde40f8b574dd2d639f9267', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 'r
[PASS ] [GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira
          medido=presencia + anillo estado='UNRECONCILED' anillo=list diagnosticos_del_run=[] -- el run sigue presente: publicar una retirada aqui daria por muerto un run que nadie ha conseguido parar
[PASS ] H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre
          fugas=[] (total 0) | control positivo con el diagnostico completo: {'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-554e6445c41b4e2b8ac11a824c85f47a', 'daemon_generation_current': 'gen-snapNew-fb3bbd673f29499da7881b512d9f81
[PASS ] H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre
          fugas=[] | control positivo: {'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-554e6445c41b4e2b8ac11a824c85f47a', 'daemon_generation_current': 'gen-snapNew-fb3bbd673f29499da7881b512d9f8129', 'generation_changed': False} --
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
          olores=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10-82f0a14fe3024f7d953991e9256ece1b', 'generation_changed': None, 'event': 'admin_reconcile', 'reason': 'admin_reconciled', 'decision': 'confirmed', 'state': 'EXITED'} (contro
[PASS ] H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10b-e2ff75840d8743e99eda10191d00f453', 'generation_changed': None, 'event': 'admin_reconcile', 'reason': 'admin_reconciled', 'decision': 'confirmed', 'state': 'EXITED'} reason_es_codigo
========================================================================================================
ORACULO-CENSO OK: 40 checks, ninguno repetido
ORACULO: PASS=40 FAIL=0 UNMET=0 de 40
ORACULO-VERDE
```

### gate-extra/oracle_lote_g.py (oráculo G, SHA-256 977ab3677fb6aa772a71e7975ce8305b36f05df07b9d969f59a6de53d48bcc63)

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
          fila={'run_id': 'run-1', 'mod': '@SameMod', 'label': 'gate', 'age_s': 4430388.359, 'owner_session': 'A', 'state': 'RUNNING', 'activity_state': 'recent', 'last_activity_age_s': 0.0, 'daemon_generation_at_launch': None, 'daemon_generation_cur
[PASS ] N15-RETROCESO una observacion sin binding mas vieja no borra un sello mas nuevo
          sembrado='recent' despues='recent' -- si cae a 'unknown', una observacion de hace un minuto ha borrado una actividad acreditada posterior, que es exactamente lo que P4 prohibe
[PASS ] N16-SIN-BINDING-MANDA una observacion sin binding posterior si publica unknown
          sembrado='recent' despues='unknown' -- si sigue 'recent', el arreglo de N15 ha desactivado P3 y volvemos a publicar frescura que nadie ha acreditado
[PASS ] N17-BINDING-MUERE-CON-SU-RUN un enqueue tras el EXITED durable se rechaza
          status=409 payload={'error': 'binding_retired', 'hint': 'Target instance was retired (stop, reap, or role replace). Relaunch that role via dayz_test_run so start_run mints a new instance. Resending this command will keep failing until the n
[PASS ] N18-SIN-CACHE-DE-MANIFIESTO la caja publica el estado durable, no el cacheado
          durable='UNRECONCILED' publicado='UNRECONCILED' -- si publica 'RUNNING', las filas salen de una foto vieja y la coherencia depende de haber acertado el incremento del contador en TODAS las mutaciones, incluidas las que aun no se han escrito
[PASS ] N19-SIN-DUENO-NO-DESPACHA un run RUNNING_IDLE no acepta comandos
          status=409 payload={'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt the existing run before dispatching.'} -- 200 significa que el daemon acepta una mutacion del mundo para un proceso que nadie posee, tras hab
[PASS ] N20-ADOPCION-REHABILITA tras adopt_run el mismo binding vuelve a despachar
          status=200 payload={'id': 1, 'peer': 'server', 'cmd': 'world_spawn'} -- si no es 200, el arreglo de N19 ha retirado el binding en el release y el reattach exige relanzar el servidor
[PASS ] N21-TUMBA un escritor con epoca anterior al borrado no resucita el sello
          estado='unknown' -- 'recent' significa que la escritura acreditada, muestreada ANTES de la observacion sin binding, aterrizo DESPUES y resucito el sello que P3 habia borrado
[PASS ] N22-SELLO-ANTES-DEL-DATO tras una mutacion en vuelo no se reutilizan las sondas
          sondas tras el lector=2, tras la lectura siguiente=3 -- iguales significa que las sondas calculadas contra el manifiesto de antes de la mutacion se guardaron bajo la revision de despues y un lector limpio las reutiliza: filas nuevas con son
[PASS ] N23-LECTURA-SIN-DUENO una lectura sobre un run RUNNING_IDLE se rechaza y no acredita
          status=409 payload={'error': 'run_not_owned', 'hint': 'This run has no owner (RUNNING_IDLE). Adopt the existing run before dispatching.'} eventos_nuevos=0 -- 200 significa que el binding despacha sin dueno; un evento nuevo significa que una
[PASS ] N25-RELEASE-CERCA-ANTES-DE-PUBLICAR un poll durante el release no se lleva la cola
          poll=200 commands=[] -- un comando entregado significa que el release publico RUNNING_IDLE antes de cercar y vaciar bajo el lock del loopback, y el bridge se llevo una mutacion de un dueno que ya no existe
[PASS ] N26-POLL-REVALIDA una transicion a RUNNING_IDLE en la ventana del poll no entrega
          poll=200 commands=[] -- un comando entregado significa que el poll copio la cola con dueno y la entrego sin dueno: la transicion de la reparacion no cerco, y el poll no revalido al retomar el lock
[PASS ] N28-CERCO-NO-SE-ACUMULA retirar un run borra su cerco
          _fenced_runs tras el reap=[] -- una marca logica que sobrevive a la retirada fisica de su run se acumula hasta el reinicio
[PASS ] N29-EXEC-ENFORCE-RUN-NOT-OWNED perder la carrera con un release devuelve run_not_owned
          status=409 error='run_not_owned' -- 200 seria despacho sin dueno; 'enqueue_cancelled' es fail-closed pero colapsa el motivo que P6 exige
[PASS ] N30-CREDITO-EN-VUELO un credito aceptado antes del cerco no aterriza despues
          activity_state='unknown' durable='RUNNING_IDLE' -- 'recent' significa que un run sin dueno publica frescura por un comando que el cerco descarto: el epoch se muestreo fuera del lock y ni el cerco ni la retirada dejan tumba
[PASS ] N31-RETIRE-CIERRA-LO-QUE-DESCARTA un exec retirado por stop queda auditado como discarded
          registros=[('allowed', 1), ('discarded', 1)] -- sin 'discarded' para id=1: la retirada tiro la cola sin cerrar operaciones ni auditar; el pin del coordinador vive hasta el timeout
[PASS ] N32-ESTADO-ILEGIBLE-RECHAZA un enqueue con el manifiesto ilegible no despacha
          status=503 payload={'error': 'run_state_unavailable'} eventos_nuevos=0 -- 200 con el estado durable ilegible es fail-open: se despacha y se acredita sin saber si hay dueno
[PASS ] N34-RESULTADO-TARDIO-TRAS-RETIRE un result sin instancia tras retirar no se acepta
          store_result=409 {'error': 'binding_retired'} -- 200 significa que la retirada borro la valla de resultado y cualquiera puede cerrar un comando despachado a un run muerto
[PASS ] N35-RETIRAR-ANTES-DE-PERSISTIR un result tardio durante la retirada terminal no se acepta
          store_result=409 {'error': 'binding_retired'} con el manifiesto ya EXITED -- 200 significa que el binding seguia vivo tras la transicion durable: el metodo unico del anillo persiste primero y retira despues, al reves que P4
[PASS ] N36-FRONTERA-TERMINAL un credito viejo no reaparece tras la limpieza de EXITED
          sellos de run-1 tras el credito tardio=[] -- la limpieza terminal borro la tumba y el escritor que acepto antes de la retirada aterrizo despues
[PASS ] N37-ESTADO-MALFORMADO-RECHAZA un registro durable sin estado no autoriza el enqueue
          status=503 payload={'error': 'run_state_unavailable'} eventos_nuevos=0 -- 200 con un registro sin `state` es fail-open: None se lee como permiso
[PASS ] N38-SIN-LIFECYCLE-NO-DESPACHA un binding BOUND sin lifecycle no encola ni entrega
          enqueue=503 payload={'error': 'run_state_unavailable'} poll_commands=[] -- 200 o una entrega significan despacho sin ninguna prueba durable de que el run esta RUNNING
[PASS ] N39-FRONTERA-TAMBIEN-PARA-EL-FALLO un audit fallido tras la limpieza terminal no recrea unknown
          _activity_unknown de run-1 tras el fallo tardio=[] -- la rama `not ok` anadio sticky sin comparar el epoch con la frontera terminal
[PASS ] N40-ADOPCION-TRAS-PERSISTENCIA-FALLIDA adoptar no entrega un run mudo
          adopt={'error': 'binding_retired_pending_reap', '_http_status': 409, 'hint': "This run's instance binding was retired before a terminal persist landed. Reap or recover the run (reap_dead_run / reap_dead_runs / admin reconcile) so the durabl
========================================================================================================
ORACULO: PASS=48 FAIL=0 UNMET=0 de 48
ORACULO-VERDE
```

### gate/suite.sh

```
test_process_lifecycle : Ran 180 tests in 2.499s

 OK

test_box_occupancy : Ran 81 tests in 1.047s

 OK

test_lifecycle_http : Ran 8 tests in 4.703s

 OK

test_run_reaper : Ran 7 tests in 0.044s

 OK

test_bug104_reap_under_quarantine : Ran 10 tests in 0.082s

 OK

test_task7_final_lifecycle_regressions : Ran 8 tests in 0.065s

 OK

test_dayz_test_tool : Ran 52 tests in 0.227s

 OK

test_dayz_test_value_error_codes : Ran 8 tests in 0.050s

 OK

test_mcp_tools : Ran 45 tests in 7.304s

 OK

test_effective_schema_catalog : Ran 5 tests in 0.001s

 OK

test_loopback : Ran 70 tests in 0.906s

 OK

SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Tests nuevos sobrepuestos a `ws-frozen-r5/tools` (copia `/tmp/r6-frozen-1055`; 6 FAIL + 2 preservación). Líneas de FAIL:

(A) P22' `test_bound_without_lifecycle_rejects_enqueue_and_holds_poll`:
```
AssertionError: 200 == 200 : {'id': 1, 'peer': 'client', 'cmd': 'camera_get'}
```
(A) P22' `test_bound_without_lifecycle_rejects_exec_enforce`:
```
AssertionError: 200 == 200 : {'id': 1, 'peer': 'server', 'cmd': 'exec_enforce'}
```
(A) P22' `test_exec_enforce_without_lifecycle_is_run_state_unavailable`:
```
AssertionError: 200 == 200 : {'id': 1, 'peer': 'server', 'cmd': 'exec_enforce'}
```
(A) P22' `test_poll_without_lifecycle_holds_bound_commands`:
```
AssertionError: Lists differ: [{'id': 1, 'cmd': 'camera_get', 'args': {}}] != []
```

(B) P20' `test_failed_audit_after_terminal_frontier_does_not_recreate_unknown`:
```
AssertionError: ('', 'run-existing') unexpectedly found in {('', 'run-existing')}
```
(B) P20' `test_failed_audit_after_frontier_still_leaves_sticky`: **preservación** (OK en frozen-r5: la rama `not ok` ya añadía sticky cuando `epoch > tumba`).

(C) P19' `test_adopt_after_failed_terminal_persist_directs_to_reap`:
```
AssertionError: True == True : {'ok': True, 'run_id': 'run-existing', 'state': 'RUNNING'}
```

`test_legacy_queue_without_binding_still_dispatches`: **preservación** (OK en frozen-r5: cola legacy sin binding sigue despachando).

Preservación (OK en el árbol ya cambiado; N34/N35/N30/N36/N37/N32 y N20 verdes en el oráculo G 48/48; H 40/40): `test_store_result_without_instance_after_retire_is_rejected` (N34), `test_in_flight_credit_does_not_land_after_fence` (N30), `test_unreadable_manifest_rejects_enqueue_without_credit` (N32), `test_malformed_durable_state_rejects_enqueue_without_credit` (N37), `test_release_owner_drains_pending_queue_and_adopt_rehabilitates` (N20), `test_adopt_requires_idle_complete_match_and_clean_quarantine`.

## LO QUE NO PUDE VERIFICAR

- No corrí la suite completa del árbol (el brief lo prohíbe). Solo `gate/suite.sh` (11 módulos, 474 tests) y los unittest del write-set (`test_process_lifecycle`, `test_box_occupancy`, `test_loopback`: 331 OK).
- No hay proceso DayZ ni bridge real: N38/N39/N40 y (A)-(C) son fixtures (ServerState sin lifecycle, barrera en `audit`/`manifest.replace`).
- No ejercí `/lifecycle/adopt` HTTP con `binding_retired_pending_reap`; el handler reenvía el dict de `adopt_run` y el 409 viaja en `_http_status`.
- No reconstruí el binding (prepare/confirm): los procesos del fixture N40 están muertos (`process_not_found`); reconstruir habría dicho `ok` sobre un destino ausente.
- `install_bound_peer` ahora adjunta un stub despachable. No medí tests fuera de la suite acotada que pongan `lifecycle = None` *después* de instalar y esperen el despacho antiguo.
- No inyecté un `run_bindings_retired` que lance; el except de `_bindings_retired_pending_reap` entonces deja adoptar (el camino de los mocks sin el método).

## DISPUTAS

Ninguna. El hash de `gate-extra/oracle_lote_g.py` medido en esta ronda es `977ab3677fb6aa772a71e7975ce8305b36f05df07b9d969f59a6de53d48bcc63`, el sello del brief (`977ab3677fb6aa77…`). N39 sigue leyendo `_activity_unknown` (no se renombró).
