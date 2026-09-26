VERDICT: FINDINGS

| id | severity | claim | summary |
|---|---|---|---|
| F-01 | BLOCKING | C1 | El caso causal evita la tool pública exigida por la ficha, por lo que un default público 200→0 reintroduce BUG-086 y deja intactos los tres tests nuevos. |
| F-02 | BLOCKING | C5 | Los límites fijos 64/4096 y el largo máximo 64 se derivan del propio código o no se ejercitan en su borde; varios mutantes contractualmente inválidos sobreviven al guardián de límites. |
| F-03 | BLOCKING | C7/C8 | Los dos diferenciales comparan polls con colas vacías; un mutante que impide entregar comandos sólo cuando recibe un censo inválido/no acreditado conserva exactamente sus respuestas y pasa ambos guardianes. |
| F-04 | BLOCKING | C9 | La costura lee la primera línea que parece declarar la constante, incluso si es un comentario; puede validar 19 nombres correctos mientras el bridge compila y envía un censo que el decoder rechaza. |
| F-05 | NON-BLOCKING | other | PollCapabilityIngressTest está declarado después de unittest.main(), así que ejecutar directamente test_loopback.py omite silenciosamente los nueve tests de M06. |

Los cuatro hallazgos BLOCKING invalidan evidencia/guardianes, no demuestran que la implementación productiva actual esté fallando. Cada uno incluye una reproducción estática ejecutada y un mutante exacto para la ronda 2.

## F-01 — El caso causal no atraviesa el default público

### Qué está mal

La ficha exige llamar inmediatamente a la tool pública wait_for sin lookback_lines (plans/inbox-20260830/12-fb-20260829-030056-d73b.md:26). El caso entregado llama directamente a server.execute_wait_for (tools/tests/test_wait_for.py:336-342). Por tanto acredita el default interno de tools/dayz_mcp/server.py:2036-2045, pero no el default ni el forwarding de la tool registrada en tools/dayz_mcp/server.py:4149-4173.

Esto importa porque la única prueba pública que encuentra una respuesta anterior pasa lookback_lines=200 explícitamente (tools/tests/test_wait_for_marker.py:75-89). Ningún test bajo tools/tests contiene una llamada pública causal que omita ese argumento. El catálogo que escribe “lookback_lines=200” es una fila manual sin campos verificables (tools/dayz_mcp/effective_schema_catalog.py:78-85; tools/tests/test_effective_schema_catalog.py:107-112), no una lectura de la firma real.

### Repro ejecutable y salida observada

[EXACT] Sonda PowerShell ejecutada desde la raíz:

    $planLine=(Get-Content -LiteralPath 'plans/inbox-20260830/12-fb-20260829-030056-d73b.md')[25]
    $test=Get-Content -Raw -LiteralPath 'tools/tests/test_wait_for.py'
    $new=$test.Substring($test.IndexOf('class WaitForBug086EvidenceTest'))
    [pscustomobject]@{
      plan_line_26=$planLine.Trim()
      new_class_build_app_refs=([regex]::Matches($new,'\bbuild_app\b')).Count
      new_class_call_tool_refs=([regex]::Matches($new,'\.call_tool\(')).Count
      new_class_execute_wait_for_refs=([regex]::Matches($new,'server\.execute_wait_for\(')).Count
    } | Format-List

[EXACT] Salida observada:

    plan_line_26                    : ... llamar inmediatamente la tool pública
                                      wait_for(log_matches,pattern=needle) sin lookback_lines ...
    new_class_build_app_refs        : 0
    new_class_call_tool_refs        : 0
    new_class_execute_wait_for_refs : 3

[EXACT] Mutante mínimo, sólo en una copia desechable:

    tools/dayz_mcp/server.py:4155
    -        lookback_lines: int = 200,
    +        lookback_lines: int = 0,

El test nuevo sigue llamando al helper cuyo default permanece en 200; una invocación MCP que omita el argumento pasa 0 al helper y vuelve a tomar el marker en EOF. Comando exacto solicitado al orquestador para ronda 2:

    Push-Location tools
    & .\.venv-mcp\Scripts\python.exe -m unittest -v tests.test_wait_for.WaitForBug086EvidenceTest tests.test_wait_for_marker.WaitForMarkerTest
    Pop-Location

### Por qué los tests existentes no lo cazan

WaitForBug086EvidenceTest no construye build_app ni usa call_tool. WaitForMarkerTest sólo ejerce el caso preexistente con 200 explícito; sus llamadas sin 200 llevan marker, por lo que el lookback se ignora (tools/tests/test_wait_for_marker.py:91-113). Así, el mutante rompe exactamente la secuencia pública pedida por la ficha sin tocar el sujeto de los tres tests nuevos.

## F-02 — Los límites fijos se verifican contra sí mismos

### Qué está mal

El contrato fija literalmente 64 nombres, 4096 bytes y nombres de 1 a 64 caracteres (plans/inbox-20260830/05-fb-20260829-023649-8f8c.md:40). El código los materializa en tools/dayz_mcp/loopback.py:174-176 y los aplica en :197-205. Sin embargo:

- at_limit y over se construyen desde loopback.POLL_CAPS_MAX_NAMES (tools/tests/test_loopback.py:1519-1523). Cambiar 64 por 65 mueve simultáneamente el límite productivo y las entradas del test.
- El caso de bytes usa POLL_CAPS_MAX_BYTES + 1 (tools/tests/test_loopback.py:1524-1528). Cambiar 4096 por 4097 mueve también el estímulo; cambiar > por >= sigue rechazando su entrada de 4097 bytes.
- Esa entrada es además un único nombre de 4097 caracteres, fuera del máximo de 64; no existe un censo gramaticalmente válido de exactamente 4096 bytes.
- No hay caso de nombre válido de 64 caracteres ni rechazo de uno de 65, de modo que {0,63}→{0,64} queda invisible.

### Repro ejecutable y salida observada

[EXACT] Sonda independiente del borde válido de 4096 bytes:

    $names = for($i=0; $i -lt 64; $i++) {
      $prefix='a'+$i.ToString('00')
      $target=if($i -eq 63){64}else{63}
      $prefix + ('x' * ($target-$prefix.Length))
    }
    $raw=$names -join ','
    [pscustomobject]@{
      names=$names.Count
      unique=($names | Sort-Object -Unique).Count
      utf8_bytes=[Text.Encoding]::UTF8.GetByteCount($raw)
      min_name=($names | Measure-Object Length -Minimum).Minimum
      max_name=($names | Measure-Object Length -Maximum).Maximum
      all_match=(($names | Where-Object {$_ -cnotmatch '^[a-z][a-z0-9_]{0,63}$'}).Count -eq 0)
    } | Format-List

[EXACT] Salida observada:

    names      : 64
    unique     : 64
    utf8_bytes : 4096
    min_name   : 63
    max_name   : 64
    all_match  : True

La entrada usada actualmente por el test dio:

    existing_byte_probe_bytes         : 4097
    existing_byte_probe_name_length   : 4097
    existing_byte_probe_grammar_match : False

[EXACT] La simulación literal de la tabla y de sus constructores, cambiando sólo cada condición indicada, produjo:

    constant_mutant_65_4097_table_passes      : True
    constant_mutant_at_limit                  : ok
    constant_mutant_over                      : too_many
    constant_mutant_byte_probe                : oversized
    regex_accepts_65_char_mutant_table_passes : True
    comparator_ge_mutant_table_passes         : True
    comparator_ge_existing_4097_probe         : oversized
    comparator_ge_valid_4096_fixture          : oversized

[EXACT] Mutantes mínimos a ejecutar por separado sobre una copia desechable:

    tools/dayz_mcp/loopback.py:174   POLL_CAPS_MAX_NAMES = 64   -> 65
    tools/dayz_mcp/loopback.py:175   POLL_CAPS_MAX_BYTES = 4096 -> 4097
    tools/dayz_mcp/loopback.py:176   {0,63}                     -> {0,64}
    tools/dayz_mcp/loopback.py:197   > POLL_CAPS_MAX_BYTES      -> >= POLL_CAPS_MAX_BYTES

Comando exacto solicitado para cada mutante en ronda 2:

    Push-Location tools
    & .\.venv-mcp\Scripts\python.exe -m unittest -v tests.test_loopback.PollCapabilityIngressTest
    Pop-Location

### Por qué los tests existentes no lo cazan

Los expected de conteo y bytes se desplazan con las constantes bajo prueba. La tabla literal sí es independiente, pero sólo contiene nombres cortos y censos pequeños (tools/tests/test_loopback.py:1475-1487). La costura actual también queda muy por debajo de los bordes: 21/329 y 19/255. Ninguna de esas filas discrimina los cuatro mutantes anteriores.

## F-03 — El diferencial “no bloquea comandos” usa colas vacías

### Qué está mal

Los dos tests que pretenden probar que el censo no bloquea crean cuatro ServerState nuevos y llaman record_poll sin encolar un solo comando (tools/tests/test_loopback.py:1567-1592). Comparar dos payloads commands=[] sólo prueba que el censo no cambia una respuesta vacía. No prueba la propiedad reclamada: que un comando ya en cola sigue entregándose.

La ruta real sí tiene un discriminador barato y estable: enqueue_command está definido en tools/dayz_mcp/loopback.py:1263-1273, los comandos read-only se enrutan tanto a cola bound como legacy en :1090-1099, y record_poll los extrae en :1867-1969.

### Repro ejecutable y salida observada

[EXACT] Sonda PowerShell ejecutada sobre los dos métodos diferenciales:

    $test = Get-Content -Raw -LiteralPath 'tools/tests/test_loopback.py'
    $m06 = $test.Substring($test.IndexOf('class PollCapabilityIngressTest'))
    $a = $m06.IndexOf('def test_unaccredited_poll_stays_unknown_and_is_answered_the_same')
    $b = $m06.IndexOf('def test_census_is_not_inherited_across_generations')
    $diff = $m06.Substring($a, $b-$a)
    [pscustomobject]@{
      differential_enqueue_calls=([regex]::Matches($diff,'\.enqueue_command\(')).Count
      differential_record_poll_calls=([regex]::Matches($diff,'\.record_poll\(')).Count
      differential_commands_assertions=([regex]::Matches($diff,'\["commands"\]')).Count
      differential_new_states=([regex]::Matches($diff,'ServerState\(')).Count
    } | Format-List

[EXACT] Salida observada:

    differential_enqueue_calls       : 0
    differential_record_poll_calls   : 4
    differential_commands_assertions : 0
    differential_new_states          : 4

[EXACT] Mutante mínimo a insertar inmediatamente después de tools/dayz_mcp/loopback.py:1851, sobre copia desechable:

    if caps is not None:
        parsed_caps, _reason = parse_poll_caps(caps)
        if not accredited or parsed_caps is None:
            return 200, {"commands": [], "delay_ms": 0, "bind": bind_label}

La llamada a _record_poll_caps_locked ya ocurrió, por lo que todos los asserts de state/reason permanecen correctos. Con las colas vacías de los tests, el early return devuelve exactamente el mismo payload que la ruta base. Con un query_player_state encolado, impide su entrega sólo en el lado con census —y, como el bridge vuelve a anunciarlo en cada poll, acaba expirando en cola—, violando C7.

[EXACT] El estímulo discriminante que falta es:

    for state in (plain, with_caps):
        enqueue_status, _ = state.enqueue_command("query_player_state", {})
        self.assertEqual(enqueue_status, 200)
    base_status, base_payload = plain.record_poll("server")
    status, payload = with_caps.record_poll("server", caps="entities_query")
    self.assertEqual((status, payload), (base_status, base_payload))
    self.assertEqual(len(payload["commands"]), 1)

Para la variante acreditada se aplica bind_both_peers a ambos estados y se conservan instance=INST_SERVER, source_pid=41001. Comando exacto solicitado en ronda 2:

    Push-Location tools
    & .\.venv-mcp\Scripts\python.exe -m unittest -v tests.test_loopback.PollCapabilityIngressTest.test_unaccredited_poll_stays_unknown_and_is_answered_the_same tests.test_loopback.PollCapabilityIngressTest.test_a_malformed_census_is_answered_the_same_as_none
    Pop-Location

### Por qué los tests existentes no lo cazan

En una cola vacía, “entregar normalmente” y “cortar antes del bucle de entrega” producen el mismo status, bind, delay_ms y commands=[]. El mutante también conserva unknown/unaccredited y unknown/malformed porque registra el censo antes de cortar. La calibración de C8 sólo mata una forma de bloqueo que altera la respuesta vacía; no mata la forma que pierde trabajo real.

## F-04 — La costura puede leer un comentario en vez de la constante compilada

### Qué está mal

_enforce_census toma la primera línea que contiene el texto const_name + " =" y concatena sus literales sin eliminar comentarios ni exigir una declaración única (tools/tests/test_loopback.py:1490-1510). El guardián del bridge cliente comparte el defecto: CAPS_DECL_RE.search toma la primera coincidencia en todo el source y tampoco elimina comentarios (tools/tests/test_bridge_client_capabilities.py:36-39,88-125).

El wire real no manda la línea encontrada por el test: manda CLIENT_POLL_CAPS desde StartPoll (addon/scripts/5_Mission/MCPClientBridge.c:417-424). Por ello ambos tests pueden acordar sobre un señuelo y no observar la constante que compila Enforce.

### Repro ejecutable y salida observada

[EXACT] Edición in-memory ejecutada: anteponer una copia comentada de la línea 168 actual y, en la copia activa inmediatamente posterior, añadir BROKEN. No se escribió el fichero.

    -	protected const string CLIENT_POLL_CAPS = "action_use,camera_get,camera_set,drive_probe_client,engine_set,key_press,player_respawn," + "restore_gameplay,ui_click,ui_dialog,ui_focus,ui_reload_layout,ui_set_text,ui_tree," + "vehicle_control,vehicle_get_in_client,vehicle_release,vehicle_telemetry,vehicle_trace";
    +	// protected const string CLIENT_POLL_CAPS = "action_use,camera_get,camera_set,drive_probe_client,engine_set,key_press,player_respawn," + "restore_gameplay,ui_click,ui_dialog,ui_focus,ui_reload_layout,ui_set_text,ui_tree," + "vehicle_control,vehicle_get_in_client,vehicle_release,vehicle_telemetry,vehicle_trace";
    +	protected const string CLIENT_POLL_CAPS = "action_use,camera_get,camera_set,drive_probe_client,engine_set,key_press,player_respawn," + "restore_gameplay,ui_click,ui_dialog,ui_focus,ui_reload_layout,ui_set_text,ui_tree," + "vehicle_control,vehicle_get_in_client,vehicle_release,vehicle_telemetry,vehicle_trace,BROKEN";

[EXACT] La sonda reprodujo tanto el selector de M06 como el primer search del test cliente y analizó por separado la línea activa. Salida observada:

    first_matching_line_is_comment : True
    m06_equals_client_parser       : True
    m06_names                      : 19
    m06_bytes                      : 255
    m06_decoder_reason             : ok
    active_names                   : 20
    active_decoder_reason          : malformed
    active_last_name               : BROKEN

BROKEN es texto de string válido para el compilador, pero no satisface [a-z][a-z0-9_]{0,63}; el bridge lo URL-encodearía y parse_poll_caps lo marcaría malformed.

Comando exacto solicitado tras aplicar esa edición sólo en copia desechable:

    Push-Location tools
    & .\.venv-mcp\Scripts\python.exe -m unittest -v tests.test_loopback.PollCapabilityIngressTest.test_the_ingress_accepts_what_the_bridges_actually_announce tests.test_bridge_client_capabilities.BridgeClientCapabilitiesTest
    Pop-Location

### Por qué los tests existentes no lo cazan

Ambos parsers eligen el comentario válido. El test cliente sigue comparando esos 19 nombres contra Dispatch y verify_poll_wire sólo confirma que StartPoll usa el identificador CLIENT_POLL_CAPS; no resuelve cuál declaración activa alimenta ese identificador (tools/tests/test_bridge_client_capabilities.py:151-170). El test servidor sí limpia comentarios y exige una declaración única (tools/tests/test_bridge_server_capabilities.py:86-102,160-177), pero no protege MCPClientBridge.c.

## F-05 — La ejecución directa omite la clase nueva

### Qué está mal

tools/tests/test_loopback.py llama unittest.main() en :1457-1458 y sólo después declara PollCapabilityIngressTest en :1513. En ejecución directa, unittest.main() descubre lo definido hasta ese punto y termina el proceso; la clase nueva nunca llega a definirse. unittest discovery por importación sí la ve, por eso el full suite informado pudo contar los nueve casos.

### Repro ejecutable y salida observada

[EXACT] Sonda PowerShell:

    $test=Get-Content -Raw -LiteralPath 'tools/tests/test_loopback.py'
    $main=$test.IndexOf('if __name__ == "__main__":')
    $m06=$test.IndexOf('class PollCapabilityIngressTest')
    [pscustomobject]@{
      unittest_main_offset=$main
      m06_class_offset=$m06
      main_precedes_m06=($main -lt $m06)
    } | Format-List

[EXACT] Salida observada:

    unittest_main_offset : 58331
    m06_class_offset     : 60453
    main_precedes_m06    : True

Comandos exactos para observar la diferencia en ronda 2:

    & tools/.venv-mcp/Scripts/python.exe tools/tests/test_loopback.py -v
    Push-Location tools
    & .\.venv-mcp\Scripts\python.exe -m unittest -v tests.test_loopback.PollCapabilityIngressTest
    Pop-Location

### Por qué los tests existentes no lo cazan

La suite canónica importa el módulo y no entra en el bloque __main__, así que no detecta que la superficie de ejecución directa está truncada. Es NON-BLOCKING porque no invalida la corrida discovery ya acreditada ni el comportamiento productivo; sí vuelve engañoso el comando más obvio para aislar este fichero.

## ATACADO Y NO ROTO

- C1: F-01 rompe la afirmación en la costura pública. No pude romper la propiedad más estrecha del helper: una needle durable escrita antes de invocar execute_wait_for se ve con su default interno 200 (tools/tests/test_wait_for.py:322-348).
- C2: Intenté reducir lookback_lines=0 a “timeout sin lectura”. El callback añade una línea distinta antes de la primera sonda y los asserts exigen probes, lines_total y una entrada readable con líneas (tools/tests/test_wait_for.py:350-387). No encontré un bypass real sin falsificar deliberadamente el propio informe scanned.
- C3: Verifiqué la tabla literal 199/200, el fichero LF-terminated y los dos asserts de exactly 200 líneas (tools/tests/test_wait_for.py:305-312,389-428). El cambio lookback_lines+1 incluye OUTSIDE-201 y contradice su expected literal; no encontré otro off-by-one sobreviviente.
- C4: Confirmé que borrar sólo la rama exterior <=0 es equivalente: el fallback llama _marker_rewound y _offset_before_last_lines_in_window también devuelve EOF para <=0 (tools/dayz_mcp/server.py:1809-1810,1863-1872). Los otros dos mutantes descritos son conductualmente distintos. No ejecuté las corridas por la prohibición expresa de tests.
- C5: F-02 rompe la cobertura de los límites cuantitativos. La tabla sí rompe mayúsculas, guion, espacio, miembro vacío y duplicado con expected literales (tools/tests/test_loopback.py:1475-1487); no encontré un fallo del decoder actual contra esas filas.
- C6: No encontré lectura de _peer_caps fuera del lock: se escribe dentro del with de record_poll (tools/dayz_mcp/loopback.py:1780-1851) y se presenta dentro del with de status_snapshot (:2409-2435). El cambio gen-one→gen-two da stale_generation (:1594-1609). Además, bind_both_peers instala bindings BOUND e identidad PID verificable (tools/tests/fence_helpers.py:10-25; tools/dayz_mcp/loopback.py:989-1018), y record_poll sólo acredita tras validar ese PID (:1824-1831): la ruta acreditada asumida por el autor sí se alcanza.
- C7: F-03 rompe la prueba diferencial. No encontré que la implementación actual bloquee: _record_poll_caps_locked se ejecuta antes de las salidas y la entrega no consulta caps (tools/dayz_mcp/loopback.py:1849-1869).
- C8: Los seis mutantes declarados no parecen equivalentes adicionales: creer un no acreditado altera state, omitir generación altera gen-two, aceptar mayúsculas/duplicados contradice la tabla, devolver rechazo por inválido altera el diferencial vacío y usar >= en el tope de nombres rechaza su fila at_limit. F-02 y F-03 muestran mutantes distintos que sobreviven, por lo que “cada propiedad tiene un guardián que la ve de verdad” sigue siendo demasiado amplio.
- C9: F-04 rompe la robustez del seam. Con el source actual, la lectura sí produce exactamente server=21/329 con entities_query y client=19/255 con ui_dialog; ambos valores actuales son aceptados por el decoder.
- Write-set: git diff-tree mostró sólo tools/tests/test_wait_for.py para 48298d1, y tools/dayz_mcp/loopback.py más tools/tests/test_loopback.py para 60e6f1d. Coincide con plans/inbox-20260830/00-execution-dag.md:41,57 y con el OWNS especial de la ficha 12 en plans/inbox-20260830/12-fb-20260829-030056-d73b.md:14.

## LO QUE NO PUDE VERIFICAR

- No ejecuté Python ni ningún test, ni siquiera los focalizados: la orden prohíbe hacerlo en este sandbox. Los comandos exactos que debe correr el orquestador en ronda 2 están en cada hallazgo. Por ello no presento como “observada” ninguna salida verde/roja de mutantes; las salidas observadas de este informe son sondas PowerShell de source y simulaciones in-memory.
- No compilé el mutante Enforce de F-04 ni construí PBO. La reproducción sólo depende de semántica ordinaria de comentario de línea/string y de los parsers leídos; el gate final debe confirmar que la copia mutada compila y que los dos tests focalizados quedan verdes.
- No convertí el caso daemon_generation=None en hallazgo. ServerState nace con None (tools/dayz_mcp/loopback.py:821), pero el daemon productivo siempre lo sustituye por un UUID nuevo (tools/dayz_mcp/daemon.py:332,342-351), y Runtime desecha el LoopbackServer completo al parar (tools/dayz_mcp/server.py:558-578). No encontré una transición productiva entre dos generaciones representadas ambas por None. Si existe otro consumidor que reutilice el mismo LoopbackServer tras stop/start, no quedó acreditado en los archivos autorizados.
- No perseguí los dos fallos preexistentes del full suite ni la ausencia de loopback.py en PACKAGED_MODULES/native_bundle.py: ambos fueron dados como hechos/fuera de estos commits.
- No hice una prueba de carrera con threads. La enumeración completa de referencias a _peer_caps sólo mostró inicialización, escritura locked y lectura locked; no apareció una ruta concreta que justificara una reproducción concurrente.
