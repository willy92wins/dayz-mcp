## HECHO

Anillo y registro. `RetiredRunDiagnostic` (`tools/dayz_mcp/process_lifecycle.py:522-530`) es un dataclass congelado con exactamente ocho campos: `run_id`, `daemon_generation_at_launch`, `daemon_generation_current`, `generation_changed`, `event`, `reason`, `decision`, `state`. El anillo es `collections.deque(maxlen=32)` en `ProcessLifecycle._retired_diagnostics` (`:929-931`), bajo `_activity_lock` (mismo orden que hoy: `_operation_lock → _activity_lock`). Cabecera en `:929-930`: «Retired-run diagnostics live in daemon memory and start empty after a restart; the audit file is never read to reconstruct them.» Un único método `_retire_run_diagnostic` (`:978-998`) hace `appendleft` (más reciente primero; el tope tira el más viejo).

Campo de RunRecord. `daemon_generation_at_launch: str | None = None` al FINAL (`:444`). `from_payload` lo lee (`:466`); ausente → `None`. `start_run` lo acredita al crear el run (`:1642`). La construcción posicional de 12 argumentos no se desplaza.

Proyección. `_generation_projection` (`:245-254`) es pura: `generation_changed = launch != current` sólo con lanzamiento acreditado; si no, `null`, nunca `false`. La usan `status()` (`:2757-2768` vía `_projected_run` `:1000-1002`), `public_status()` (`:2771-2783`) y `_derive_box` (`:277`). El anillo viaja en ambos status; `box_occupancy` no lo publica.

Seis caminos que alimentan el anillo (retirada real a EXITED). Grep:

```
rg -n "_retire_run_diagnostic|_retire_run_bindings" tools/dayz_mcp/process_lifecycle.py
```

1. `stop_run` (`:1930`) → `_retire_run_diagnostic` `:2180` (éxito `stopped/stopped/EXITED`). Bindings en `:1981` (UNRECONCILED) y `:2049` (STOPPING) no entran al anillo: un stop fallido deja el run presente.
2. `_reap_run_locked` (`:2641`) → `:2667` junto a `_retire_run_bindings` `:2666` (`run_reaped` / `all_processes_gone_or_foreign` / `reaped` / `EXITED`).
3. `begin_release_owner` (`:2270`) → `:2383` junto a bindings EXITED `:2382`. Bindings UNRECONCILED `:2335` y `:2363` no alimentan el anillo.
4. `repair_recovery_fault` (`:2426`) → `:2492` junto a `:2491`.
5. `repair_manifest_recovery` (`:2513`) → `:2587` junto a `:2586`.
6. `admin_reconcile` (`:3009`) → `:3111` junto a `:3110` (sólo si `state == "EXITED"`).

Sobre de stop. `execute_dayz_test_stop` (`tools/dayz_mcp/dayz_test_tool.py:817`) toma **un** snapshot pre-dispatch (`:830`) y clasifica `resolve_stop_run` sin tocarlo: presente no activo → dict `run_not_active` copiando las tres generaciones (`:836-840`); ausente con un único diagnóstico → dict `run_not_found` (`:841-845`); UUID nunca visto o diagnóstico ausente/ambiguo → `DayzTestToolError("run_not_found")`. El sobre no lleva `retired_run_diagnostics`. `server.py` no se tocó: el dict cruza porque no es excepción.

## GATE

```
========================================================================================================
[PASS ] H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo
          faltan=[] fila={'daemon_generation_at_launch': 'gen-A-094104e189e9487c989fc1665fe9496c', 'daemon_generation_current': 'gen-A-094104e189e9487c989fc1665fe9496c', 'generation_changed': False} generacion inyectada='gen-A-094104e189e9487c989fc1665fe9496c' -- los tres campos se derivan de la generacion que recibe el construc
[PASS ] H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones
          faltan=[] box={'daemon_generation_at_launch': 'gen-A-094104e189e9487c989fc1665fe9496c', 'daemon_generation_current': 'gen-A-094104e189e9487c989fc1665fe9496c', 'generation_changed': False} status={'daemon_generation_at_launch': 'gen-A-094104e189e9487c989fc1665fe9496c', 'daemon_generation_current': 'gen-A-094104e189e9487
[PASS ] H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva
          faltan=[] fila={'daemon_generation_at_launch': 'gen-1-3eba0989d5c54228ad787eb2ad188feb', 'daemon_generation_current': 'gen-2-613d2bf76b2d48248921c917020ff11c', 'generation_changed': True} lanzamiento='gen-1-3eba0989d5c54228ad787eb2ad188feb' actual='gen-2-613d2bf76b2d48248921c917020ff11c' -- si at_launch sale igual a la
[PASS ] H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false
          faltan=[] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': 'gen-L2-9b925852a0e54f5fa278fdba4bfc4a45', 'generation_changed': None} campos quitados del manifiesto=['daemon_generation_at_launch'] -- generation_changed=False afirmaria continuidad que nadie acredito; el valor fail-closed es null
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
          error=None runs=['12345678-1234-4234-8234-1234567890ab'] -- un campo nuevo que from_payload no tolere ausente convierte TODO manifiesto preexistente en invalid_run_manifest en el primer arranque tras la entrega
[PASS ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
          tras _clone='gen-D2b-3fce2671a9f34ef3856a9d5581cf7dfb' tras recargar='gen-D2b-3fce2671a9f34ef3856a9d5581cf7dfb' esperado='gen-D2b-3fce2671a9f34ef3856a9d5581cf7dfb' -- _clone hace asdict->from_payload: un campo que from_payload no lea se pierde en silencio en cada get()/list_runs(), sin lanzar nada
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
          launch_operation_id='87654321-4321-4321-8321-ba0987654321' launch_request_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' launch_acknowledged=False -- el campo nuevo va al FINAL con default; insertarlo antes desplaza el unico call-site posicional
[PASS ] H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2a-ce009341d09048249a64763c7fbc7de3', 'daemon_generation_current': 'gen-H2a-ce009341d09048249a64763c7fbc7de3', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped',
[PASS ] H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop
          diagnosticos=[{'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2b-22496d26383f48048a984175f803b1ac', 'daemon_generation_current': 'gen-H2b-22496d26383f48048a984175f803b1ac', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision':
[PASS ] H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes
          retirados=40 anillo=list publicados=32 (tope 32) faltan_de_los_recientes=[] sobran_viejos=[] -- un anillo sin tope crece con cada retirada y viaja entero en cada status
[PASS ] H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta
          hallazgos=[] diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H2d-0f794b019bc34c7291a9b378cf6e5ec1', 'daemon_generation_current': 'gen-H2d-0f794b019bc34c7291a9b378cf6e5ec1', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision':
[PASS ] H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia
          anillo tras reiniciar=[] run_presente=False eventos_de_retirada_en_el_jsonl=1 -- publicar un diagnostico aqui significaria haberlo reconstruido leyendo events.jsonl, que es lo que D1 prohibe; la lista vacia es la respuesta correcta
[PASS ] [GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez
          aperturas bajo C:\Users\guill\AppData\Local\Temp\tmpa40oqaqj\runtime\audit: [] (events.jsonl=2983 bytes; control positivo vio 3 aperturas) -- leer el jsonl desde la ruta de lectura rompe la escritura del writer en Windows; D1 lo prohibe
[PASS ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_active', 'daemon_generation_at_launch': 'gen-snapOld-f9735d1bfe28496b81f92b2c87d5e1c0', 'daemon_generation_current': 'gen-snapNew-07cc992ebadc4b9a87158d0452c241ff', 'generation_changed': False} -- hoy resolve_sto
[PASS ] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
          snapshot={'daemon_generation_at_launch': 'gen-snapOld-f9735d1bfe28496b81f92b2c87d5e1c0', 'daemon_generation_current': 'gen-snapNew-07cc992ebadc4b9a87158d0452c241ff', 'generation_changed': False} sobre={'daemon_generation_at_launch': 'gen-snapOld-f9735d1bfe28496b81f92b2c87d5e1c0', 'daemon_generation_current': 'gen-snapN
[PASS ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
          ok | devuelto={'status': 'failed', 'run_id': '12345678-1234-4234-8234-1234567890ab', 'error_code': 'run_not_found', 'daemon_generation_at_launch': 'gen-snapOld-f9735d1bfe28496b81f92b2c87d5e1c0', 'daemon_generation_current': 'gen-snapNew-07cc992ebadc4b9a87158d0452c241ff', 'generation_changed': False} -- los tres campos
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
          ok | diagnostico_real={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H3x-d62ebda68bb04d209ac194266df2c763', 'daemon_generation_current': 'gen-H3x-d62ebda68bb04d209ac194266df2c763', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 's
[PASS ] H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro
          ok -- hoy DayzTestToolError se traduce a ToolError (server.py:3054-3055) y el sobre no llega nunca al cliente
[PASS ] [GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado
          kind=raise tipo=ToolError texto='Error executing tool dayz_test_stop: run_not_found' campos_inventados=[]
[PASS ] H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero
          claves que arrastran el anillo=[] sobre=['daemon_generation_at_launch', 'daemon_generation_current', 'error_code', 'generation_changed', 'run_id', 'status'] -- el anillo lleva hasta 32 runs ajenos; su sitio es el status publico
[PASS ] H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5a-43f0d97817c342c887d074214c1450bb', 'daemon_generation_current': 'gen-H5a-43f0d97817c342c887d074214c1450bb', 'generation_changed': False, 'event': 'lifecycle_stop_outcome', 'reason': 'stopped', 'decision': 'stopped', '
[PASS ] H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop
          diagnostico={'run_id': '12345678-1234-4234-8234-1234567890ab', 'daemon_generation_at_launch': 'gen-H5b-4237483cad6740529379e44fdd609e4c', 'daemon_generation_current': 'gen-H5b-4237483cad6740529379e44fdd609e4c', 'generation_changed': False, 'event': 'run_reaped', 'reason': 'all_processes_gone_or_foreign', 'decision': 'r
[PASS ] [GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira
          medido=presencia + anillo estado='UNRECONCILED' anillo=list diagnosticos_del_run=[] -- el run sigue presente: publicar una retirada aqui daria por muerto un run que nadie ha conseguido parar
========================================================================================================
ORACULO-CENSO OK: 29 checks, ninguno repetido
ORACULO: PASS=29 FAIL=0 UNMET=0 de 29
ORACULO-VERDE
```

```
test_process_lifecycle : Ran 117 tests in 1.804s  OK
test_box_occupancy : Ran 75 tests in 1.057s  OK
test_lifecycle_http : Ran 8 tests in 4.682s  OK
test_run_reaper : Ran 7 tests in 0.042s  OK
test_bug104_reap_under_quarantine : Ran 10 tests in 0.081s  OK
test_task7_final_lifecycle_regressions : Ran 8 tests in 0.074s  OK
test_dayz_test_tool : Ran 49 tests in 0.204s  OK
test_dayz_test_value_error_codes : Ran 8 tests in 0.049s  OK
test_mcp_tools : Ran 45 tests in 7.194s  OK
test_effective_schema_catalog : Ran 5 tests in 0.001s  OK
test_loopback : Ran 65 tests in 0.990s  OK
SUITE-ACOTADA OK
```

## CONTROL POSITIVO

Corridos ANTES del cambio (`unittest` de las clases nuevas): 19 tests, FAILED (failures=12, errors=5). Línea que demostraba el rojo:

§2 (A) un test por camino
- `test_stop_run_feeds_the_retired_diagnostic_ring` (`tools/tests/test_process_lifecycle.py:2461`): `AssertionError: None is not an instance of <class 'list'> : retired_run_diagnostics missing or not a list: None`
- `test_reap_run_feeds_the_retired_diagnostic_ring` (`:2474`): misma línea, `retired_run_diagnostics missing or not a list: None`
- `test_begin_release_owner_feeds_the_retired_diagnostic_ring` (`:2502`): misma línea
- `test_repair_recovery_fault_feeds_the_retired_diagnostic_ring` (`:2532`): misma línea
- `test_repair_manifest_recovery_feeds_the_retired_diagnostic_ring` (`:2574`): misma línea
- `test_admin_reconcile_feeds_the_retired_diagnostic_ring` (`:2607`): misma línea

§2 (B) public_status
- `test_status_and_public_status_project_generation_on_present_run` (`:2372`): `KeyError: 'daemon_generation_at_launch'`

§2 (C) mutantes del gate no calibrados
- `test_ring_caps_at_32_keeping_the_most_recent` (`:2635`): `AssertionError: None is not an instance of <class 'list'>`
- `test_diagnostic_has_no_timestamp_or_path_keys` (`:2667`): `retired_run_diagnostics missing or not a list: None`

§2 (D) manifiesto preexistente real
- `test_legacy_runs_json_without_field_loads_and_survives_get_list_replace` (`:2416`): `AssertionError: 'missing' is not None` (getattr del campo ausente devolvía el default `"missing"`)

Resto de tests nuevos, también rojos antes:
- `test_generation_changed_is_null_never_false_without_launch_generation` (`:2389`): `KeyError: 'daemon_generation_at_launch'`
- `test_from_payload_round_trip_keeps_launch_generation` (`:2409`): `AttributeError: 'RunRecord' object has no attribute 'daemon_generation_at_launch'`
- `test_new_lifecycle_publishes_an_empty_ring` (`:2696`): `AssertionError: None != []`
- `test_failed_stop_does_not_enter_the_ring` (`:2684`): falló porque el anillo no existía (`retired_run_diagnostics missing`); no medía aún el fail-closed de H5
- `test_box_occupancy_projects_the_same_generation_fields_as_status` (`tools/tests/test_box_occupancy.py:2057`): `AssertionError: 'daemon_generation_at_launch' not found in {'run_id': 'run-box', ...}`
- `test_present_inactive_run_returns_structured_envelope` (`tools/tests/test_dayz_test_tool.py:1817`): `DayzTestToolError: run_not_active`
- `test_absent_run_with_one_diagnostic_returns_run_not_found_envelope` (`:1838`): `DayzTestToolError: run_not_found`

Preservación (ya verdes antes del cambio; siguen verdes):
- `test_unknown_uuid_still_raises_run_not_found` (`:1860`): ok
- `test_ambiguous_diagnostics_still_raise_run_not_found` (`:1865`): ok

## LO QUE NO PUDE VERIFICAR

- No corrí la suite completa de `tools/tests` (el brief lo prohíbe). G2 cubre los once módulos acotados.
- No arranqué un daemon vivo ni hablé HTTP: `public_status()` lo consume `daemon.py:655`, y los tests/oráculo lo miden in-process, no contra un proceso real.
- El `runs.json` de §2(D) es un fichero escrito en la forma del árbol anterior, no un manifiesto copiado de una instalación de producción.
- No repetí la saturación Windows `os.replace` vs handle abierto de `events.jsonl` (D1); el oráculo sí comprobó cero lecturas en status/box con control positivo del espía.
- No hay test nuevo en `test_mcp_tools.py`: el transporte público lo midió el oráculo (`H4-TOOL-TRANSPORTA-EL-SOBRE`) sustituyendo el runtime, no un cliente MCP real.
- No ejecuté la auditoría `rigorous-data-audit` que `gate/GATES.md` declara release-gate humano; este lote no la corre.

## DISPUTAS

Ninguna. Cero UNMET al cierre. El anillo no se engancha a cada `_retire_run_bindings` (hay llamadas en stop/release que dejan el run presente: UNRECONCILED / STOPPING); se engancha a la retirada EXITED de los seis caminos. Eso es lo que H5 exige y lo que el oráculo midió.
