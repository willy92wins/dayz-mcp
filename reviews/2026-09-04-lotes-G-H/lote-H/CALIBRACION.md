# Calibración del gate de lote H

La escribe la sesión redactora, **antes** de que exista la implementación. Todo lo que sigue
está medido; lo que no, está en `LO QUE NO PUDE VERIFICAR` al final.

Comando exacto de cada medida:

```
cd <arbol>/tools
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe" \
  <ruta>/oracle.py
```

Árboles usados:

| Alias | Ruta | Qué es |
| --- | --- | --- |
| REFERENCIA | `scratchpad/lote-G/integ/tools/` | copia congelada de `tools/` tras la ronda 5 del lote G |
| PROTOTIPO | `scratchpad/lote-H/draft/proto/tools/` | copia mía de REFERENCIA + parche mínimo. **No viaja.** |

---

## 1. Salida literal contra el árbol de REFERENCIA

`ORACULO: PASS=11 FAIL=12 UNMET=6 de 29` — proceso con RC=1.

```
========================================================================================================
[FAIL ] H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo
          faltan=['daemon_generation_at_launch', 'daemon_generation_current', 'generation_changed'] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': None, 'generation_changed': None} generacion inyectada='gen-A-45f1470cfe9d490d9bb9ef8b0ad51c2d' -- los tres campos se derivan de la generacion que recibe el c
[FAIL ] H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones
          faltan=['daemon_generation_at_launch', 'daemon_generation_current', 'generation_changed'] box={'daemon_generation_at_launch': None, 'daemon_generation_current': None, 'generation_changed': None} status={'daemon_generation_at_launch': None, 'daemon_generation_current': None, 'generation_changed': None} -- las dos proyec
[FAIL ] H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva
          faltan=['daemon_generation_at_launch', 'daemon_generation_current', 'generation_changed'] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': None, 'generation_changed': None} lanzamiento='gen-1-e6e54493b96f41229d227a028be82858' actual='gen-2-85ba431a16944422943509f1ad0ba9bf' -- si at_launch sale ig
[FAIL ] H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false
          faltan=['daemon_generation_at_launch', 'daemon_generation_current', 'generation_changed'] fila={'daemon_generation_at_launch': None, 'daemon_generation_current': None, 'generation_changed': None} campos quitados del manifiesto=[] -- generation_changed=False afirmaria continuidad que nadie acredito; el valor fail-closed
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
          error=None runs=['12345678-1234-4234-8234-1234567890ab'] -- un campo nuevo que from_payload no tolere ausente convierte TODO manifiesto preexistente en invalid_run_manifest en el primer arranque tras la entrega
[FAIL ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
          el manifiesto persistido no lleva daemon_generation_at_launch: claves=['label', 'launch_acknowledged', 'launch_operation_id', 'launch_request_sha256', 'mission', 'mod', 'owner_lease_id', 'owner_session_id', 'processes', 'profiles', 'run_id', 'state'] -- sin persistirlo, la generacion de lanzamiento no sobrevive a un re
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
          launch_operation_id='87654321-4321-4321-8321-ba0987654321' launch_request_sha256='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' launch_acknowledged=False -- el campo nuevo va al FINAL con default; insertarlo antes desplaza el unico call-site posicional
[FAIL ] H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto
          diagnosticos=None -- se espera exactamente uno para 12345678-1234-4234-8234-1234567890ab, con la generacion de lanzamiento 'gen-H2a-fc4c353582a342bfbeea16142f88c413'
[FAIL ] H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop
          diagnosticos=None -- si solo stop_run alimenta el anillo, un run reapeado desaparece sin dejar rastro y su dayz_test_stop no puede devolver mas que una excepcion pelada
[FAIL ] H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes
          retirados=40 anillo=NoneType publicados=0 (tope 32) faltan_de_los_recientes=['seed-08', 'seed-09', 'seed-10', 'seed-11', 'seed-12'] sobran_viejos=[] -- un anillo sin tope crece con cada retirada y viaja entero en cada status
[UNMET] H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta
          no hay diagnostico que inspeccionar: None. El sujeto de este check todavia no existe.
[FAIL ] H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia
          anillo tras reiniciar=None run_presente=False eventos_de_retirada_en_el_jsonl=1 -- publicar un diagnostico aqui significaria haberlo reconstruido leyendo events.jsonl, que es lo que D1 prohibe; la lista vacia es la respuesta correcta
[PASS ] [GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez
          aperturas bajo C:\Users\guill\AppData\Local\Temp\tmpw1m9v71q\runtime\audit: [] (events.jsonl=2983 bytes; control positivo vio 3 aperturas) -- leer el jsonl desde la ruta de lectura rompe la escritura del writer en Windows; D1 lo prohibe
[FAIL ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
          no es dict: DayzTestToolError | devuelto=DayzTestToolError('run_not_active') -- hoy resolve_stop_run levanta DayzTestToolError('run_not_active') y el llamador se queda sin run_id ni generacion
[UNMET] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
          no hay sobre que inspeccionar: DayzTestToolError: run_not_active. El sujeto todavia no existe.
[FAIL ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
          no es dict: DayzTestToolError | devuelto=DayzTestToolError('run_not_found') -- los tres campos salen del diagnostico, no de un recomputo
[PASS ] [GUARDA] H3-MUT-PODADO-NO-ES-NOT-ACTIVE un run ausente nunca se publica como run_not_active
          codigo='run_not_found' (kind=raise) -- run_not_active afirma presencia; el run no esta en runs y solo lo acredita un diagnostico retirado
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
[UNMET] H3-INTEGRACION-STATUS-REAL el status del lifecycle alimenta el sobre
          el fixture no dejo el run ausente con UN diagnostico: presente=False diagnosticos=0. Sin esa evidencia no se puede medir el encaje M17 -> M19.
[FAIL ] H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro
          la tool levanto ToolError: Error executing tool dayz_test_stop: run_not_active -- hoy DayzTestToolError se traduce a ToolError (server.py:3054-3055) y el sobre no llega nunca al cliente
[PASS ] [GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado
          kind=raise tipo=ToolError texto='Error executing tool dayz_test_stop: run_not_found' campos_inventados=[]
[UNMET] H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero
          no hay sobre que inspeccionar: ToolError: Error executing tool dayz_test_stop: run_not_found. El sujeto todavia no existe.
[UNMET] H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED
          no hay diagnostico que inspeccionar; el sujeto todavia no existe
[UNMET] H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop
          el fixture no reapeo o no dejo diagnostico: reaped=['12345678-1234-4234-8234-1234567890ab'] diagnosticos=0
[PASS ] [GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira
          medido=solo presencia (el anillo aun no existe, esa mitad no se ha medido) estado='UNRECONCILED' anillo=NoneType diagnosticos_del_run=[] -- el run sigue presente: publicar una retirada aqui daria por muerto un run que nadie ha conseguido parar
========================================================================================================
ORACULO-CENSO OK: 29 checks, ninguno repetido
ORACULO: PASS=11 FAIL=12 UNMET=6 de 29
```

---

## 2. Por qué cada rojo es rojo

Ninguno de los 12 FAIL es un rojo de andamiaje: en los 12 el sujeto es alcanzable y lo que
falta es el producto. Los detalles literales de cada línea están arriba.

| Check | Qué se midió, y qué falta |
| --- | --- |
| `H1-STATUS-GEN-PRESENTE` | `status()` publica `dataclasses.asdict(RunRecord)`: 12 claves, ninguna de generación. Los tres campos salen `faltan=[...]`. |
| `H1-BOX-GEN-PRESENTE` | `box_occupancy()` publica 8 claves por run (`run_id, mod, label, age_s, owner_session, state, activity_state, last_activity_age_s`). Ninguna de generación. |
| `H1-GEN-CAMBIADA` | El fixture SÍ sobrevive al reinicio (mismo `runs.json`, `ProcessLifecycle` nuevo con otra generación) y el run sigue en `status()`; lo que falta son los tres campos. Es el check que fuerza a **persistir** la generación de lanzamiento: sin manifiesto no hay forma de saber en qué generación arrancó. |
| `H1-FAIL-CLOSED-SIN-GENERACION` | Mismo motivo. Su guarda anti-vacuidad no se disparó porque los campos faltan del todo. |
| `D2-MUT-FROM-PAYLOAD-NO-LEE` | El manifiesto persistido no lleva `daemon_generation_at_launch`: `claves=['label', 'launch_acknowledged', 'launch_operation_id', 'launch_request_sha256', 'mission', 'mod', 'owner_lease_id', 'owner_session_id', 'processes', 'profiles', 'run_id', 'state']`. |
| `H2-DIAG-TRAS-STOP` | El fixture para el run (`ok=True`), lo poda (deja de estar en `runs`) y `status()` devuelve `retired_run_diagnostics=None`: la clave no existe. |
| `H2-DIAG-TRAS-REAP` | Igual, por el camino del reaper: `reap_dead_runs()` devuelve el run, y no queda diagnóstico. |
| `H2-TOPE-32-POR-RECENCIA` | El fixture retira **40 runs en una pasada** (`reaped=40`) y el anillo es `NoneType`. El tope no se puede violar porque no hay anillo. |
| `H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO` | Rojo por `None != []`: la clave no existe. La otra mitad SÍ se midió: el `events.jsonl` real llevaba 1 evento de retirada, así que el check discrimina «no reconstruye» de «no había nada». |
| `H3-RUN-NOT-ACTIVE-SOBRE` | `execute_dayz_test_stop` levanta `DayzTestToolError('run_not_active')`; el llamador se queda sin `run_id` ni generación. |
| `H3-RUN-NOT-FOUND-SOBRE-CON-DIAG` | Levanta `DayzTestToolError('run_not_found')` aunque el snapshot traiga el diagnóstico exacto. |
| `H4-TOOL-TRANSPORTA-EL-SOBRE` | La tool pública devuelve `ToolError: Error executing tool dayz_test_stop: run_not_active` (`server.py:3054-3055` traduce la excepción tipada). |

---

## 3. Mutantes codificados como checks

Los seis mutantes que la ficha nombra están codificados, y **cuatro están calibrados** contra
el PROTOTIPO: se aplicaron uno a uno, se midió, y se restauró la base.

| Mutante de la ficha | Check que lo caza | Calibrado |
| --- | --- | --- |
| recomputar `generation_changed` | `H3-MUT-RECOMPUTA-GENERATION-CHANGED` | **Sí** — M1 |
| convertir un run podado en `run_not_active` | `[GUARDA] H3-MUT-PODADO-NO-ES-NOT-ACTIVE` | **Sí** — M2 |
| fabricar campos para UUID desconocido | `[GUARDA] H3-FAIL-CLOSED-UUID-NUNCA-VISTO` | **Sí** — M3 |
| `from_payload` no lee el campo | `D2-MUT-FROM-PAYLOAD-NO-LEE` | **Sí** — M4 |
| más de 32 diagnósticos | `H2-TOPE-32-POR-RECENCIA` | No — ver más abajo |
| timestamp o path en un diagnóstico | `H2-SIN-TIMESTAMP-NI-PATH` | No — ver más abajo |

**Cómo se detecta que los campos se COPIAN y no se recalculan.** El snapshot que inyecta el
fixture lleva una terna **deliberadamente incoherente**: `daemon_generation_at_launch` y
`daemon_generation_current` distintas, con `generation_changed=False`. Cualquier recomputo daría
`True`. Copiar es la única forma de devolver `False`. Las dos generaciones son `uuid4` frescos
por proceso, así que una implementación que las escriba como literal tampoco pasa.

### Resultado literal de cada mutante (sobre el PROTOTIPO)

- **M1 — recomputar `generation_changed`** (`envelope["generation_changed"] = at_launch != current`):

```
[FAIL ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
[FAIL ] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
[FAIL ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
ORACULO: PASS=13 FAIL=12 UNMET=4 de 29     (base: PASS=17 FAIL=8 UNMET=4)
```

- **M2 — un run podado se publica como `run_not_active`** (`error_code` fijo):

```
[FAIL ] [GUARDA] H3-MUT-PODADO-NO-ES-NOT-ACTIVE un run ausente nunca se publica como run_not_active
[FAIL ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
ORACULO: PASS=15 FAIL=10 UNMET=4 de 29
```

- **M3 — fabricar campos para UUID nunca visto** (sin evidencia, `source = {campo: None}`):

```
[FAIL ] [GUARDA] H3-FAIL-CLOSED-UUID-NUNCA-VISTO sin evidencia se levanta run_not_found pelado
[FAIL ] [GUARDA] H3-FAIL-CLOSED-DIAG-AMBIGUO dos diagnosticos del mismo run no acreditan nada
ORACULO: PASS=14 FAIL=11 UNMET=4 de 29
```

- **M4 — `from_payload` deja de leer el campo** (el campo se declara y se persiste, pero
  `from_payload` no lo lee):

```
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
[FAIL ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
          tras _clone=None tras recargar=None esperado='gen-D2b-...'
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
ORACULO: PASS=16 FAIL=9 UNMET=4 de 29
```

M4 es el más caro de los cuatro y el único data-crítico: el campo **existe, se persiste y se
publica**, y aun así se pierde en cada `get()`/`list_runs()` porque `_clone` hace
`asdict→from_payload`. Ninguna excepción, ningún test rojo, ningún log. Nótese que el guardia
de compatibilidad se queda **verde** durante el mutante: los dos checks miden cosas distintas y
hacen falta los dos.

**Control positivo de los cuatro**: con la base del prototipo restaurada, los checks vuelven a
verde (`PASS=17 FAIL=8 UNMET=4`). 4 de 4 cazados, 0 falsos negativos.

**Los dos que NO están calibrados, y por qué, en vez de disimularlo.** «Más de 32 diagnósticos»
y «timestamp o path en un diagnóstico» atacan código que **no existe ni en el prototipo**: el
anillo de M17. Calibrarlos hoy exigiría escribir M17 dentro del mutante, o sea calibrar una
conjetura mía en vez de la entrega. **Los corre la sesión orquestadora contra el código
entregado, antes de aceptar el lote.** Un mutante contra sus rojos actuales no probaría nada,
porque el rojo actual es «el sujeto está ausente».

---

## 4. Prueba de alcanzabilidad (regla 7)

**Pregunta**: ¿puede `H3-RUN-NOT-ACTIVE-SOBRE` ponerse verde, o el gate pide algo imposible?

**Prototipo**: ~50 líneas en `dayz_test_tool.py` y nada más. `execute_dayz_test_stop` guarda el
snapshot pre-dispatch en una variable, captura `DayzTestToolError` de `resolve_stop_run` y, si
hay una fuente exacta (la fila presente, o un único diagnóstico retirado) con los tres campos,
devuelve el sobre copiándolos; si no la hay, re-levanta.

**Medida**:

| Árbol | Veredicto |
| --- | --- |
| REFERENCIA | `ORACULO: PASS=11 FAIL=12 UNMET=6 de 29` |
| PROTOTIPO, sólo el sobre en `dayz_test_tool.py` | `ORACULO: PASS=16 FAIL=9 UNMET=4 de 29` |
| PROTOTIPO + `daemon_generation_at_launch` persistido en `RunRecord` | `ORACULO: PASS=17 FAIL=8 UNMET=4 de 29` |

Con sólo el sobre pasan a verde **cinco** checks —`H3-RUN-NOT-ACTIVE-SOBRE`,
`H3-MUT-RECOMPUTA-GENERATION-CHANGED`, `H3-RUN-NOT-FOUND-SOBRE-CON-DIAG`,
`H4-TOOL-TRANSPORTA-EL-SOBRE`, `H4-DIAGNOSTICOS-SOLO-EN-STATUS`— y **ningún `[GUARDA]` se
rompe**. Con el campo persistido pasa además `D2-MUT-FROM-PAYLOAD-NO-LEE`. Los ocho rojos que
quedan son exactamente M17 (anillo + proyección de generación en `status`/`box_occupancy`), que
el prototipo no implementa a propósito.

Dos hallazgos de diseño que salen de esta prueba y están en el ledger:

1. **El sobre va en `execute_dayz_test_stop`, no en `resolve_stop_run`.** Con el prototipo
   aplicado, los 11 módulos acotados siguen en verde; `resolve_stop_run` conserva sus tests de
   API pública (`tests/test_dayz_test_tool.py:357-370` exige que levante `run_not_active`).
2. **Ni el sobre ni el campo nuevo de `RunRecord` rompen un test entregado.**

Salida literal del prototipo: `calib/proto.txt`. El árbol: `draft/proto/`, con su
`NO-VIAJA.txt`. **No se entrega.**

---

## 5. Suite acotada (G2)

Módulos y por qué cada uno: `process_lifecycle.py` → `test_process_lifecycle`,
`test_box_occupancy`, `test_lifecycle_http`, `test_run_reaper`,
`test_bug104_reap_under_quarantine`, `test_task7_final_lifecycle_regressions`;
`dayz_test_tool.py` → `test_dayz_test_tool`, `test_dayz_test_value_error_codes`; `server.py` →
`test_mcp_tools`, `test_effective_schema_catalog`; ruta `/lifecycle/status` de `loopback.py` →
`test_loopback`.

Contra el árbol de REFERENCIA (copia pelada de `tools/`, **sin** `addon/` ni
`process-guard.ps1`):

```
test_process_lifecycle : Ran 90 tests in 0.936s FAILED (errors=1)
test_box_occupancy : Ran 66 tests in 1.056s OK
test_lifecycle_http : Ran 8 tests in 4.688s OK
test_run_reaper : Ran 7 tests in 0.061s OK
test_bug104_reap_under_quarantine : Ran 10 tests in 0.093s OK
test_task7_final_lifecycle_regressions : Ran 8 tests in 0.058s OK
test_dayz_test_tool : Ran 45 tests in 0.221s OK
test_dayz_test_value_error_codes : Ran 8 tests in 0.064s OK
test_mcp_tools : Ran 45 tests in 8.227s OK
test_effective_schema_catalog : Ran 5 tests in 0.001s OK
test_loopback : Ran 64 tests in 0.917s FAILED (errors=2)
SUITE-ACOTADA ROJA
```

Contra el PROTOTIPO, que sí tiene los dos ficheros no-python y además lleva el parche:

```
test_process_lifecycle : Ran 90 tests in 1.015s OK
test_box_occupancy : Ran 66 tests in 1.087s OK
test_lifecycle_http : Ran 8 tests in 4.711s OK
test_run_reaper : Ran 7 tests in 0.050s OK
test_bug104_reap_under_quarantine : Ran 10 tests in 0.080s OK
test_task7_final_lifecycle_regressions : Ran 8 tests in 0.081s OK
test_dayz_test_tool : Ran 45 tests in 0.241s OK
test_dayz_test_value_error_codes : Ran 8 tests in 0.045s OK
test_mcp_tools : Ran 45 tests in 7.506s OK
test_effective_schema_catalog : Ran 5 tests in 0.001s OK
test_loopback : Ran 64 tests in 0.986s OK
SUITE-ACOTADA OK
```

**Los dos rojos de la REFERENCIA no son de código.** `test_process_lifecycle` lee
`tools/process-guard.ps1` (`tests/test_process_lifecycle.py:1772`) y `test_loopback` lee
`addon/scripts/5_Mission/MCPBridge.c` y `MCPClientBridge.c` dos niveles por encima de `tests/`
(`tests/test_loopback.py:1495-1503`, `tests/_addon_paths.py`). La copia congelada sólo tiene
`dayz_mcp/`, `_session_coordination/` y `tests/`. Copiados esos dos ficheros, los 11 módulos
salen `OK`. **G2 sólo es alcanzable en un workspace con el layout `<ws>/{addon,tools,gate}`**, y
eso está dicho en el ledger.

---

## LO QUE NO PUDE VERIFICAR

1. **Cuatro de los seis caminos que retiran runs no están gateados.** El oráculo ejercita
   `stop_run` y `_reap_run_locked` (vía `reap_dead_runs`). **No** ejercita
   `begin_release_owner`, `repair_recovery_fault`, `repair_manifest_recovery` ni
   `admin_reconcile`. Los tres primeros retiran dentro de callbacks de limpieza con
   precondiciones que no supe fabricar en un fixture barato; `admin_reconcile` está gateado por
   TTY. **Un anillo enganchado sólo a esos dos caminos pasaría el gate y dejaría mudos los otros
   cuatro.** La orquestadora debería exigir en la revisión que el diff enganche los seis
   (`grep _retire_run_bindings`), porque el oráculo no lo puede acreditar.
2. **Los mutantes «>32 diagnósticos» y «timestamp/path» no están calibrados** (§3). Sus checks
   están escritos y hoy salen rojo/UNMET por ausencia del sujeto; que discriminen de verdad no
   está medido.
3. **Nada de esto se ha probado in-game ni contra un daemon vivo.** Todo son fixtures en
   proceso. En particular, la afirmación de D1 sobre el `PermissionError` de `os.replace` en
   Windows la **heredo del lote anterior**: no la he vuelto a reproducir en esta sesión.
   `[GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL` mide que no se abre el fichero, no que abrirlo
   rompa la escritura.
4. **`public_status()` no está gateado.** El oráculo mide `status()`, que es lo que consume
   `resolve_stop_run` por `/lifecycle/status`. `public_status()` (`process_lifecycle.py:2543`)
   alimenta el payload de `daemon.status_provider` (`daemon.py:655`) y **no** se comprueba que
   lleve los tres campos ni el anillo. Si la ficha los quiere ahí también, falta un check.
5. **El tope de 32 se mide por recencia de RETIRADA en una única pasada de reap**, donde el
   orden lo fija `manifest.list_runs()` (ordenado por `run_id`) y los ids del fixture están
   nombrados para que ese orden coincida con el de retirada. **No** se ha medido la recencia
   entre retiradas de caminos distintos ni con concurrencia.
6. **El `H2-SIN-TIMESTAMP-NI-PATH` es una heurística**, no una prueba: prohíbe claves cuyo
   nombre contenga `time/stamp/path/dir/file/profil/argv/cwd/mission/utc/epoch/at_` y valores
   que parezcan ISO-8601, ruta Windows/POSIX o epoch (>1e9). Un timestamp codificado de otra
   forma (un entero pequeño, base64) se le escapa.
7. **No he ejecutado nada contra el repo real ni contra `lote-G/ws/`.** Los dos ficheros
   copiados del repo (`tools/process-guard.ps1` y `addon/`) se leyeron y se copiaron al
   prototipo; el repo no se tocó. `lote-G/ws/` no se leyó ni se escribió: el modelo de estilo
   se tomó de `lote-G/oracle.py`, que es la copia de la redactora anterior.
8. **`H1-BOX-GEN-PRESENTE` comparte su check fail-closed** con `H1-STATUS-GEN-PRESENTE`
   (`H1-FAIL-CLOSED-SIN-GENERACION` mide el legacy sobre `status()`, no sobre `box_occupancy()`).
   Un fallo fail-closed que sólo afectara a la proyección del box no lo cazaría nadie.
9. **El censo del oráculo (`ORACULO-CENSO`) comprueba número y unicidad de nombres**, no que
   cada bloque haya llegado a su aserción: un bloque que muriera en su `except` sigue contando,
   como UNMET. Eso es lo pretendido, pero un UNMET masivo por un fallo de entorno se vería como
   «sujeto ausente» si no se leen los detalles.
