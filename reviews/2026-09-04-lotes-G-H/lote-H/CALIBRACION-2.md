# Calibración 2 del gate de lote H — los seis defectos que el gate no discriminaba

La ronda 1 dejó el gate en 29 checks. La entrega (Cursor Grok 4.6) los pasó **29/29** y la
suite acotada entera; la revisión ciega de Codex reprodujo **cinco defectos** y otro revisor
un **sexto**. Esta ronda añade **11 checks** (29 → 40) y los calibra.

Yo mismo había dejado dicho en `CALIBRACION.md` §LO QUE NO PUDE VERIFICAR que faltaban los
mutantes de persistencia y de texto libre, y que cuatro de los seis caminos de retirada no
estaban gateados. Los seis defectos caen justo ahí. Esta ronda cierra esos huecos.

Comando de cada medida:

```
cd <arbol>/tools
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" \
  <ruta>/oracle.py
```

| Alias | Ruta | Qué es |
| --- | --- | --- |
| ENTREGA | `scratchpad/lote-H/ws-frozen-r1/` | la entrega r1, congelada, SOLO LECTURA |
| PROTOTIPO | `scratchpad/lote-H/draft/proto-r2/` | copia mía de la ENTREGA + los seis arreglos mínimos. **No viaja.** |

---

## 1. Salida literal contra la ENTREGA

`ORACULO: PASS=31 FAIL=9 UNMET=0 de 40` — proceso con RC=1. Los 29 checks de la ronda 1
siguen **todos verdes**; los 9 rojos son los nuevos, y los 2 nuevos verdes son parejas de
preservación declaradas (§3).

```
========================================================================================================
[PASS ] H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo
          faltan=[] fila={'daemon_generation_at_launch': 'gen-A-c628892ab2874910acfeb4c9abb33920', 'daemon_generation_current': 'gen-A-c628892ab2874910acfeb4c9abb33920', 'generation_changed': False} generacion inyectada='gen-A-c628892ab2874910acfeb4c9abb33920' -- los tres campos se derivan de la generacion que recibe el construc
[PASS ] H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones
          faltan=[] box={'daemon_generation_at_launch': 'gen-A-c628892ab2874910acfeb4c9abb33920', 'daemon_generation_current': 'gen-A-c628892ab2874910acfeb4c9abb33920', 'generation_changed': False} status={'daemon_generation_at_launch': 'gen-A-c628892ab2874910acfeb4c9abb33920', 'daemon_generation_current': 'gen-A-c628892ab287491
[PASS ] H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva
          faltan=[] fila={'daemon_generation_at_launch': 'gen-1-fb93a6966ba740bda3582861c1ccb909', 'daemon_generation_current': 'gen-2-9e24e8fdb1bc41ac91bd877e667f116e', 'generation_changed': True} lanzamiento='gen-1-fb93a6966ba740bda3582861c1ccb909' actual='gen-2-9e24e8fdb1bc41ac91bd877e667f116e' -- si at_launch sale igual a la
[PASS ] H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false
          faltan=[] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-L2-7fed22fcdb51433daad36d31be2d094d', 'generation_changed': None} campos quitados del manifiesto=['daemon_generation_at_launch'] -- generation_changed=False afirmaria continuidad que nadie acredito; el valor fail-closed es null
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
          error=None runs=['12345678-1234-4234-8234-1234567890ab'] -- un campo nuevo que from_payload no tolere ausente convierte TODO manifiesto preexistente en invalid_run_manifest en el primer arranque tras la entrega
[PASS ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
          tras _clone='gen-D2b-28588516c3b648f2bc5304596034c177' tras recargar='gen-D2b-28588516c3b648f2bc5304596034c177' esperado='gen-D2b-28588516c3b648f2bc5304596034c177' -- _clone hace asdict->from_payload: un campo que from_payload no lea se pierde en silencio en cada get()/list_runs(), sin lanzar nada
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
          launch_operation_id='87654321-4321-4321-8321-ba0987654321' launch_request_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' launch_acknowledged=False -- el campo nuevo va al FINAL con default; insertarlo antes desplaza el unico call-site posicional
[PASS ] H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2a-a3d3ec3068bd4b158a0bd597a36936ad', 'daemon_generation_current': 'gen-H2a-a3d3ec3068bd4b158a0bd597a36936ad', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped',
[PASS ] H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2b-cdf66513cd814c728d6c1e205e24c18c', 'daemon_generation_current': 'gen-H2b-cdf66513cd814c728d6c1e205e24c18c', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 
[PASS ] H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes
          retirados=40 anillo=list publicados=32 (tope 32) faltan_de_los_recientes=[] sobran_viejos=[] -- un anillo sin tope crece con cada retirada y viaja entero en cada status
[PASS ] H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta
          hallazgos=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2d-5dc47682ebac4da78666bcadaf5ff3af', 'daemon_generation_current': 'gen-H2d-5dc47682ebac4da78666bcadaf5ff3af', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision':
[PASS ] H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia
          anillo tras reiniciar=[] run_presente=False eventos_de_retirada_en_el_jsonl=1 -- publicar un diagnostico aqui significaria haberlo reconstruido leyendo events.jsonl, que es lo que D1 prohibe; la lista vacia es la respuesta correcta
[PASS ] [GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez
          aperturas bajo C:\Users\guill\AppData\Local\Temp\tmpiy79ukfx\runtime\audit: [] (events.jsonl=2982 bytes; control positivo vio 3 aperturas) -- leer el jsonl desde la ruta de lectura rompe la escritura del writer en Windows; D1 lo prohibe
[PASS ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-6a27b5b3aed04251a26b1df309e793b7', 'daemon_generation_current': 'gen-snapNew-dabaa384c77544e1ab85b2cea36133aa', 'generation_changed': False} -- hoy resolve_sto
[PASS ] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
          snapshot={'daemon_generation_at_launch': 'gen-snapOld-6a27b5b3aed04251a26b1df309e793b7', 'daemon_generation_current': 'gen-snapNew-dabaa384c77544e1ab85b2cea36133aa', 'generation_changed': False} sobre={'daemon_generation_at_launch': 'gen-snapOld-6a27b5b3aed04251a26b1df309e793b7', 'daemon_generation_current': 'gen-snapN
[PASS ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-6a27b5b3aed04251a26b1df309e793b7', 'daemon_generation_current': 'gen-snapNew-dabaa384c77544e1ab85b2cea36133aa', 'generation_changed': False} -- los tres campos 
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
          ok | diagnostico_real={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H3x-f0315922c9be4f098b8cc22e5151452f', 'daemon_generation_current': 'gen-H3x-f0315922c9be4f098b8cc22e5151452f', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 's
[PASS ] H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro
          ok -- hoy DayzTestToolError se traduce a ToolError (server.py:3054-3055) y el sobre no llega nunca al cliente
[PASS ] [GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado
          kind=raise tipo=ToolError texto='Error executing tool dayz_test_stop: run_not_found' campos_inventados=[]
[PASS ] H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero
          claves que arrastran el anillo=[] sobre=['daemon_generation_at_launch', 'daemon_generation_current', 'error_code', 'generation_changed', 'run_id', 'status'] -- el anillo lleva hasta 32 runs ajenos; su sitio es el status publico
[PASS ] H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5a-620038785f0246fd906cf58513a47855', 'daemon_generation_current': 'gen-H5a-620038785f0246fd906cf58513a47855', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped', '
[PASS ] H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5b-2b3f4f59e0d7455a803a8b06fdf99d22', 'daemon_generation_current': 'gen-H5b-2b3f4f59e0d7455a803a8b06fdf99d22', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 'r
[PASS ] [GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira
          medido=presencia + anillo estado='UNRECONCILED' anillo=list diagnosticos_del_run=[] -- el run sigue presente: publicar una retirada aqui daria por muerto un run que nadie ha conseguido parar
[FAIL ] H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre
          fugas=["sin 'daemon_generation_at_launch' -> sobre con {'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-snapNew-dabaa384c77544e1ab85b2cea36133aa', 'generation_changed': False}", "sin 'daemon_generation_current' -> sobre con {'daemon_generation_at_launch': 'gen-snapOld-6a27b5b3aed04251a26b1df309e7
[FAIL ] H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre
          fugas=["sin 'daemon_generation_at_launch' -> sobre con {'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-snapNew-dabaa384c77544e1ab85b2cea36133aa', 'generation_changed': False}", "sin 'daemon_generation_current' -> sobre con {'daemon_generation_at_launch': 'gen-snapOld-6a27b5b3aed04251a26b1df309e7
[FAIL ] H7-FALLO-DE-PERSISTENCIA-NO-PUBLICA un replace roto no consume una posicion
          caminos con fuga=["reap: desaloja 1 diagnosticos reales (['fill-00']), 1 del objetivo, run en 'RUNNING'", "repair_recovery_fault: desaloja 1 diagnosticos reales (['fill-00']), 1 del objetivo, run en 'STARTING'", "admin_reconcile: desaloja 1 diagnosticos reales (['fill-00']), 1 del objetivo, run en 'UNRECONCILED'"] | sa
[FAIL ] H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR al persistir, el anillo aun no conoce el run
          caminos que publican ANTES de persistir=['reap', 'repair_recovery_fault', 'admin_reconcile'] | por camino={'reap': [True], 'repair_recovery_fault': [True], 'admin_reconcile': [True], 'stop_run': [False, False]} -- stop_run ya lo hace bien: persiste y luego publica. Es la misma invariante para los seis caminos que retir
[PASS ] H7-PERSISTENCIA-OK-SI-PUBLICA una retirada sana sigue dejando su diagnostico
          diagnosticos por camino={'reap': 1, 'repair_recovery_fault': 1, 'admin_reconcile': 1, 'stop_run': 1} estado final={'reap': 'EXITED', 'repair_recovery_fault': 'EXITED', 'admin_reconcile': 'EXITED', 'stop_run': 'EXITED'} -- si esto se pone rojo, la correccion de H7 ha borrado el producto en vez de ordenarlo
[FAIL ] H7-ANILLO-NO-SE-CONSUME-CON-FALLOS 33 retiradas fallidas no desalojan historia
          pasadas=33 desalojados=['real-00', 'real-01', 'real-02'] de ['real-00', 'real-01', 'real-02'] entradas_del_objetivo=32 estado='RUNNING' -- el reaper corre cada 30 s: con una entrada por intento fallido, en poco mas de 15 min el anillo de 32 posiciones es entero el MISMO run que nunca se retiro, y la historia real esta 
[FAIL ] H8-REPARACION-REPETIDA-RECHAZADA reparar un run ya EXITED no anade otro diagnostico
          primera=True segunda={'terminal_safe': True, 'run_id': '12345678-1234-4234-8234-1234567890ab', 'state': 'EXITED', 'manifest_sha256': 'e175cd69990e1d614f08e79e2641381077a53cd9638697419466e300e397215f'} diagnosticos 1a->2a: 1->2 estado tras la 1a='EXITED' -- el segundo diagnostico duplica la cardinalidad y tumba el sobre
[FAIL ] H9-STATUS-SIN-LECTURA-RASGADA ninguna fila no terminal convive con su diagnostico
          hallazgos=["status: 12345678-1234-4234-8234-1234567890ab en runs con state='RUNNING' y a la vez en el anillo", "public_status: 12345678-1234-4234-8234-1234567890ab en runs con state='RUNNING' y a la vez en el anillo"] -- una fila RUNNING junto a un diagnostico EXITED del mismo run solo puede salir de leer el manifiesto
[PASS ] [GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA la fila EXITED y su diagnostico conviven
          fila='EXITED' diagnosticos=1 -- sin recargar, status() publica los EXITED: la ficha 16 exige que el run presente-pero-terminal siga en runs Y que status conserve su diagnostico. Esto NO es la lectura rasgada de H9.
[FAIL ] H10-ADMIN-REASON-NO-VIAJA el texto libre del operador no llega al diagnostico
          olores=["reason='C:\\\\Users\\\\alice\\\\secret\\\\events.jsonl @ 2026-09-04T05:00:00Z' parece un timestamp ISO"] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10-ea9f99fd0674430fbda228f1e601b8a0', 'generation_changed': None, 'even
[FAIL ] H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-H10b-f3e76393590d4d05a24b3c486199a565', 'generation_changed': None, 'event': 'admin_reconcile', 'reason': 'C:\\Users\\alice\\secret\\events.jsonl @ 2026-09-04T05:00:00Z', 'decision': 'co
========================================================================================================
ORACULO-CENSO OK: 40 checks, ninguno repetido
ORACULO: PASS=31 FAIL=9 UNMET=0 de 40
```

---

## 2. Los once checks nuevos, y por qué cada rojo es rojo

| Check | Hallazgo | Color | Evidencia medida |
| --- | --- | --- | --- |
| `H6-DIAG-INCOMPLETO-NO-FABRICA` | 1 | **FAIL** | Con `retired_run_diagnostics=[{"run_id": X}]` el sobre sale con los tres campos a `None`. Barre los 8 campos del contrato uno a uno, más `state` ∈ {RUNNING, RUNNING_IDLE, STOPPING, UNRECONCILED, ""}. |
| `H6-FILA-INCOMPLETA-NO-FABRICA` | 1 (extensión) | **FAIL** | La MISMA helper `_copy_generation` sirve al `run_not_active`: una fila sin proyección de generación sale igual con tres `None`. |
| `H7-FALLO-DE-PERSISTENCIA-NO-PUBLICA` | 2 | **FAIL** | `OSError` inyectado en `manifest.replace`: `reap`, `repair_recovery_fault` y `admin_reconcile` publican igual, y cada uno **desaloja un diagnóstico real** del anillo lleno. El run queda en `RUNNING`/`STARTING`/`UNRECONCILED`. |
| `H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR` | 2 | **FAIL** | En el instante de `manifest.replace`, el anillo YA conoce el run en `reap`, `repair_recovery_fault` y `admin_reconcile`; en `stop_run` no (`[False, False]`). La invariante ya está bien en un camino de seis. |
| `H7-PERSISTENCIA-OK-SI-PUBLICA` | 2 (pareja) | PASS | Preservación: una retirada **sana** sigue dejando exactamente 1 diagnóstico y el run en `EXITED`, en los cuatro caminos. Verde por diseño. |
| `H7-ANILLO-NO-SE-CONSUME-CON-FALLOS` | 6 | **FAIL** | 33 pasadas con `replace` roto: `entradas_del_objetivo=32` y los **tres** diagnósticos reales sembrados quedan desalojados. |
| `H8-REPARACION-REPETIDA-RECHAZADA` | 3 | **FAIL** | La segunda `repair_recovery_fault` sobre el run ya `EXITED` devuelve `terminal_safe=True` otra vez; el anillo pasa de 1 a 2 entradas del mismo run. |
| `H9-STATUS-SIN-LECTURA-RASGADA` | 4 | **FAIL** | Con la barrera, `status()` **y** `public_status()` publican el run en `runs` con `state='RUNNING'` y a la vez en el anillo. |
| `[GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA` | 4 (pareja) | PASS | Preservación: tras un stop y **sin recargar**, la fila `EXITED` y su diagnóstico conviven, como exige la ficha 16 (PASS 3.º). Verde por diseño. |
| `H10-ADMIN-REASON-NO-VIAJA` | 5 | **FAIL** | El `reason` del operador llega íntegro: `reason='C:\\Users\\alice\\secret\\events.jsonl @ 2026-09-04T05:00:00Z'`. |
| `H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO` | 5 (pareja) | **FAIL** | Pareja anti-sobrecorrección: el diagnóstico debe seguir existiendo y nombrando el camino, con un `reason` que sea **código cerrado** (sin espacios, ≤64, alfanumérico + `_-.`). Hoy falla sólo por esa última mitad. |

### La corrección del punto 4, aplicada

La regla que codifiqué **no** es «un diagnóstico de un run que figura en `runs` no se
publica»: `status()` publica los `EXITED` (la poda ocurre al recargar), así que fila `EXITED`
+ diagnóstico es la situación **normal** tras un stop y la ficha 16 la exige. La regla es la
corregida: **ninguna fila NO terminal** (`RUNNING`, `RUNNING_IDLE`, `STARTING`, `STOPPING`,
`UNRECONCILED`) puede convivir con un diagnóstico del mismo `run_id` en el mismo payload. El
control positivo de preservación (`[GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA`) fija la
convivencia legítima como verde, así que un arreglo que filtre de más se pone rojo.

---

## 3. Los dos verdes nuevos, declarados

Regla 6: un check verde antes de implementar no mide producto, o se reescribe o se declara.

- `H7-PERSISTENCIA-OK-SI-PUBLICA` — pareja anti-sobrecorrección de H7. Arreglar el orden
  **borrando** el diagnóstico pondría verde `H7-FALLO-DE-PERSISTENCIA` y dejaría el lote sin
  producto. Este exige que la retirada sana siga publicando uno.
- `[GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA` — control positivo de preservación de H9,
  explicado arriba.

Los 29 de la ronda 1 siguen verdes, incluidos los 11 `[GUARDA]` de entonces.

---

## 4. Calibración por mutantes: cada rojo, atado a SU defecto

Desde el PROTOTIPO en verde (40/40), se revierte **un** arreglo cada vez y se mide. Salida
literal:

```
BASE (prototipo arreglado): ORACULO: PASS=40 FAIL=0 UNMET=0 de 40
  rojos: ninguno

M1 quitar la validacion de evidencia completa (F1)
  ORACULO: PASS=38 FAIL=2 UNMET=0 de 40
  ROJO: H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre
  ROJO: H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre

M2 publicar antes de persistir en el reap (F2/F6)
  ORACULO: PASS=37 FAIL=3 UNMET=0 de 40
  ROJO: H7-FALLO-DE-PERSISTENCIA-NO-PUBLICA un replace roto no consume una posicion
  ROJO: H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR al persistir, el anillo aun no conoce el run
  ROJO: H7-ANILLO-NO-SE-CONSUME-CON-FALLOS 33 retiradas fallidas no desalojan historia

M3 quitar el guard de run ya terminal en repair_recovery_fault (F3)
  ORACULO: PASS=39 FAIL=1 UNMET=0 de 40
  ROJO: H8-REPARACION-REPETIDA-RECHAZADA reparar un run ya EXITED no anade otro diagnostico

M4 volver a componer status sin foto coherente (F4)
  ORACULO: PASS=39 FAIL=1 UNMET=0 de 40
  ROJO: H9-STATUS-SIN-LECTURA-RASGADA ninguna fila no terminal convive con su diagnostico

M5 devolver el texto libre del operador al diagnostico (F5)
  ORACULO: PASS=38 FAIL=2 UNMET=0 de 40
  ROJO: H10-ADMIN-REASON-NO-VIAJA el texto libre del operador no llega al diagnostico
  ROJO: H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino

BASE restaurada: ORACULO: PASS=40 FAIL=0 UNMET=0 de 40 rojos=ninguno
```

**5 de 5 mutantes cazados, cada uno exactamente por sus checks, sin daño colateral, y la base
vuelve a 40/40.** Eso es lo que convierte «rojo» en «rojo por la razón correcta»: cada rojo
se enciende y se apaga con su defecto y con ningún otro.

### Dos defectos del PROPIO GATE que esta calibración cazó

La primera pasada de mutantes salió mal, y ahí estaba el valor:

1. **M1 no enrojecía `H6-FILA-INCOMPLETA`.** Mi check contaba como aceptable *cualquier*
   excepción. Una implementación que indexe sin comprobar levanta `KeyError`, que cruza la
   tool pública como `dayz_test_failed:KeyError` (`server.py:3058,3063-3064`): eso no es
   fail-closed, es un crash sin código. Corregido: ahora se exige `DayzTestToolError` con el
   código exacto (`run_not_found` en el camino del diagnóstico, `run_not_active` en el de la
   fila presente, porque ahí el run **sí** está y esa sigue siendo la verdad).
2. **M2 no enrojecía ningún `H7`.** El instrumento era `status()`, y el arreglo de F4 suprime
   —a propósito— el diagnóstico de un run cuya fila sigue no terminal… que es **exactamente**
   el estado de un run cuya persistencia falló. El gate se quedaba ciego justo en el caso que
   quería ver. Corregido: los checks de consumo del anillo miden por **desalojo** (público,
   inmune al filtro: se llena el anillo con retiradas reales y se mira si el intento fallido
   las expulsa) y el de orden lee el anillo crudo (`_retired_diagnostics`), declarado como
   sonda de caja blanca que sale UNMET si ese nombre desaparece.

---

## 5. Alcanzabilidad: los seis arreglos, medidos

El PROTOTIPO aplica el mínimo sobre la ENTREGA: validación de evidencia completa en el sobre;
`_retire_run_diagnostic` **después** de `manifest.replace` en los cinco caminos que lo hacían
antes; guard de `run.state == "EXITED"` en `repair_recovery_fault`; código cerrado
`admin_reconciled` en vez del `reason` del operador; y `status()`/`public_status()`
componiendo la lista de runs y el anillo en una sola foto, suprimiendo el diagnóstico cuya
fila del mismo payload es no terminal.

| Árbol | Veredicto |
| --- | --- |
| ENTREGA | `ORACULO: PASS=31 FAIL=9 UNMET=0 de 40` |
| PROTOTIPO | `ORACULO: PASS=40 FAIL=0 UNMET=0 de 40` — `ORACULO-VERDE` |

Los 40 son satisfacibles a la vez: ninguno de los checks nuevos contradice a otro ni a los 29
de la ronda 1.

---

## 6. La suite acotada no tiene señal aquí

```
test_process_lifecycle : Ran 117 tests in 2.095s OK 
test_box_occupancy : Ran 75 tests in 1.251s OK 
test_lifecycle_http : Ran 8 tests in 4.699s OK 
test_run_reaper : Ran 7 tests in 0.051s OK 
test_bug104_reap_under_quarantine : Ran 10 tests in 0.095s OK 
test_task7_final_lifecycle_regressions : Ran 8 tests in 0.079s OK 
test_dayz_test_tool : Ran 49 tests in 0.202s OK 
test_dayz_test_value_error_codes : Ran 8 tests in 0.048s OK 
test_mcp_tools : Ran 45 tests in 7.325s OK 
test_effective_schema_catalog : Ran 5 tests in 0.001s OK 
test_loopback : Ran 65 tests in 0.973s OK 
SUITE-ACOTADA OK
```

```
test_process_lifecycle : Ran 117 tests in 1.867s OK 
test_box_occupancy : Ran 75 tests in 1.190s OK 
test_lifecycle_http : Ran 8 tests in 4.737s OK 
test_run_reaper : Ran 7 tests in 0.047s OK 
test_bug104_reap_under_quarantine : Ran 10 tests in 0.098s OK 
test_task7_final_lifecycle_regressions : Ran 8 tests in 0.058s OK 
test_dayz_test_tool : Ran 49 tests in 0.219s OK 
test_dayz_test_value_error_codes : Ran 8 tests in 0.048s OK 
test_mcp_tools : Ran 45 tests in 7.277s OK 
test_effective_schema_catalog : Ran 5 tests in 0.001s OK 
test_loopback : Ran 65 tests in 1.049s OK 
SUITE-ACOTADA OK
```

Los 11 módulos están verdes **en la ENTREGA con los seis defectos dentro** y siguen verdes
**con los seis arreglos aplicados**. G2 no discrimina ninguno de los seis: es un gate de
regresión, y como tal sigue valiendo, pero no aporta nada a esta ronda. Dicho de otra forma:
sin estos 11 checks, los seis defectos entran en producción con el gate en verde y la suite
en verde.

---

## LO QUE NO PUDE VERIFICAR

1. **`begin_release_owner` y `repair_manifest_recovery` siguen sin gate dinámico.** Los seis
   caminos publican el diagnóstico, pero el oráculo sólo conduce cuatro (`stop_run`, `reap`,
   `repair_recovery_fault`, `admin_reconcile`): el primero retira dentro de un hilo de
   limpieza y el segundo exige unos bytes de manifiesto de respaldo que no supe fabricar
   barato. En el PROTOTIPO les moví el orden **a ciegas**, y ningún check lo comprueba.
2. **`H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR` es de caja blanca**: lee `_retired_diagnostics`.
   Si el implementador renombra el anillo, el check sale **UNMET**, no verde — pero entonces
   no mide, y hay que reescribirlo, no darlo por bueno.
3. **La regla de H9 es la que fijó el coordinador, no una derivada de la ficha.** «Suprimir el
   diagnóstico» es lo que hace mi prototipo; «releer el manifiesto» o «versionar el anillo»
   también satisfarían el check. El gate fija el síntoma prohibido, no la implementación.
4. **`H6-FILA-INCOMPLETA-NO-FABRICA` es una extensión mía**, no uno de los cinco hallazgos: el
   coordinador nombró el camino del diagnóstico y yo he aplicado la misma regla D4 al camino
   de la fila, porque comparten helper. Si se considera fuera de alcance, se retira sin tocar
   nada más.
5. **El código cerrado de H10 se comprueba por FORMA, no por valor**: sin espacios, ≤64
   caracteres, alfanumérico más `_-.`. `admin_reconciled` pasa, y también pasaría cualquier
   otro token razonable. El vocabulario exacto no está fijado por el gate.
6. **La barrera de H9 es determinista pero sintética**: envuelve `manifest.list_runs` y
   dispara la retirada en la misma hebra. Reproduce el intercalado, no la concurrencia real;
   una carrera con dos hilos y otro reparto de locks podría dar formas que no he medido.
7. **Nada de esto se ha probado in-game ni contra un daemon vivo**; todo son fixtures en
   proceso.
8. **La cardinalidad del anillo se mide con `DIAG_CAP=32` del oráculo**, que es un literal del
   gate. Si la entrega cambiara el tope, `H7-ANILLO` mediría contra el número equivocado y
   habría que sincronizarlo a mano.
9. **No he vuelto a correr la suite completa (2321 tests)**, sólo los 11 módulos acotados.
10. **No he tocado `ws-frozen-r1/` ni `lote-H/ws/`**: el prototipo es una copia bajo
    `draft/proto-r2/`, con su `NO-VIAJA.txt`, y **no se entrega**.
