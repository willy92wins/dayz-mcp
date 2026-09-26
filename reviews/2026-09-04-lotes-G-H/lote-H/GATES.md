# Gates: lote H — ficha 16 (`4d66`) PASS 3.º y 4.º, ficha 17 (`7c88`) PASS 2.º

Alcance: el **sobre estructurado de `dayz_test_stop`** y los **diagnósticos de runs
retirados**. Cierra los pasos 5-8 del plan de `plans/inbox-20260830/16-fb-20260829-104608-4d66.md`
(criterios PASS de las líneas 50-51) y el 2.º PASS de `17-fb-20260829-104625-7c88.md:42`.

**Fuera de alcance a propósito**: la actividad (`activity_state`, `last_activity_age_s`) se
cerró en el lote G y **no se re-gatea aquí** (D3). La matriz `preflight × mode × run_id` de la
ficha 17 (pasos 1-3, 5-7) es otro lote: aquí sólo entra su bloque de `stop`.

Este ledger lo escribió y lo corrió la sesión redactora ANTES de delegar. **No lo edites.**

## §Producto — qué tiene que existir cuando esto esté verde

- **H1.** Para un run **presente**, `lifecycle_status` (`process_lifecycle.status()`) y
  `box_occupancy` adjuntan a cada fila `daemon_generation_at_launch`,
  `daemon_generation_current` y `generation_changed`.
- **H2.** Para runs **ya ausentes**, el status publica como máximo **32**
  `retired_run_diagnostics` por recencia, cada uno con `run_id`, generación de lanzamiento y
  actual, `event`, `reason`, `decision` y `state`, **sin timestamps ni paths**.
- **H3.** `execute_dayz_test_stop` (`tools/dayz_mcp/dayz_test_tool.py:774`) captura **un**
  snapshot pre-dispatch de status y clasifica el resultado de `resolve_stop_run`: run presente
  pero no activo → dict `status="failed"`, `run_id`, `error_code="run_not_active"` y los tres
  campos de generación **copiados** del snapshot; run podado/ausente con **un único**
  `retired_run_diagnostic` exacto → el mismo dict con `error_code="run_not_found"`; UUID nunca
  visto o diagnóstico ausente/ambiguo → `ToolError("run_not_found")` **sin campos inventados**.
  Stop sobre run activo continúa como hoy.
- **H4.** `dayz_test_stop` (`tools/dayz_mcp/server.py:3039`) transporta esos dicts íntegros;
  `retired_run_diagnostics` viaja **sólo** dentro del status público, nunca en el sobre.
- **H5.** Un `lifecycle_stop_outcome` exitoso (`reason="stopped"`, `decision="stopped"`,
  `state="EXITED"`) se distingue de `manual_cleanup_required`, `partial_cleanup`, `RUNNING`,
  `STOPPING` y `UNRECONCILED`; `run_reaped` acredita retirada **sin terminación**.

## §Decisiones heredadas — no se renegocian desde la implementación

- **D1 — la ruta de lectura NO lee `events.jsonl`.** Se desvía a propósito del paso 4 de la
  ficha. Motivo medido en la sesión anterior: en Windows, leer el fichero hace fallar la
  escritura del writer (`os.replace` contra un handle abierto → `PermissionError`), 776/776 en
  saturación. Consecuencia: los diagnósticos y la generación de lanzamiento viven **en memoria
  del daemon** (anillo acotado en `ProcessLifecycle`, alimentado por los mismos caminos que
  retiran runs) y **en el manifiesto** (generación de lanzamiento por run). Tras un reinicio el
  anillo empieza vacío y **eso se publica como está**: no se reconstruye historia.
  Gate: `[GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL` y `H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO`.
- **D2 — la compatibilidad del manifiesto es data-crítica (DZ-R9).** Un campo nuevo en
  `RunRecord` que `from_payload` no tolere ausente convierte **todo** manifiesto preexistente en
  `invalid_run_manifest` en el primer arranque. Además: la construcción posicional de
  `process_lifecycle.py:1445-1462` pasa 12 argumentos sin nombre (el campo nuevo va **al final**,
  con default), y `_clone` hace round-trip `asdict→from_payload` (`:565`), así que un campo que
  `from_payload` no lea **se pierde en silencio** en cada `get()`/`list_runs()`.
  Gate: `[GUARDA] D2-MANIFIESTO-VIEJO-CARGA`, `D2-MUT-FROM-PAYLOAD-NO-LEE`,
  `[GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA`, `H1-FAIL-CLOSED-SIN-GENERACION`.
- **D3 — la actividad no es producto de este lote.** No la toques y no la re-gatees.
- **D4 — fail-closed siempre.** Lo que no se puede acreditar se publica `null`/`unknown`, nunca
  se infiere. En particular `generation_changed=null`, **nunca `false`**, cuando no hay
  generación de lanzamiento: `false` afirmaría continuidad que nadie acreditó.

## §Estado medido antes de delegar

Salida literal del oráculo contra el árbol de referencia
(`lote-G/integ/tools/`, copia congelada de `tools/` tras la ronda 5 del lote G).
La salida completa con los detalles de cada línea está en `CALIBRACION.md`.

```
[FAIL ] H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo
[FAIL ] H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones
[FAIL ] H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva
[FAIL ] H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false
[PASS ] [GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando
[FAIL ] D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga
[PASS ] [GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio
[FAIL ] H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto
[FAIL ] H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop
[FAIL ] H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes
[UNMET] H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta
[FAIL ] H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia
[PASS ] [GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez
[FAIL ] H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado
[UNMET] H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan
[FAIL ] H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre
[PASS ] [GUARDA] H3-MUT-PODADO-NO-ES-NOT-ACTIVE un run ausente nunca se publica como run_not_active
[PASS ] [GUARDA] H3-FAIL-CLOSED-UUID-NUNCA-VISTO sin evidencia se levanta run_not_found pelado
[PASS ] [GUARDA] H3-FAIL-CLOSED-DIAG-AMBIGUO dos diagnosticos del mismo run no acreditan nada
[PASS ] [GUARDA] H3-UN-SOLO-SNAPSHOT-PRE-DISPATCH exactamente una lectura antes de despachar
[PASS ] [GUARDA] H3-STOP-ACTIVO-SIGUE-IGUAL el camino feliz no cambia
[PASS ] [GUARDA] H3-RELECTURA-POST-EJECUCION-INTACTA run_stop_failed se sigue reconciliando
[UNMET] H3-INTEGRACION-STATUS-REAL el status del lifecycle alimenta el sobre
[FAIL ] H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro
[PASS ] [GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado
[UNMET] H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero
[UNMET] H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED
[UNMET] H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop
[PASS ] [GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira
ORACULO-CENSO OK: 29 checks, ninguno repetido
ORACULO: PASS=11 FAIL=12 UNMET=6 de 29
```

**Los 11 PASS son la línea que no se puede romper**, y los 11 son `[GUARDA]`: verdes por
diseño, de preservación. No miden producto, miden que la entrega no rompa lo que ya funciona.
Los tres más caros:

- `[GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL` — es D1 en forma ejecutable, y el implementador
  está **activamente tentado** de romperlo: el paso 4 de la propia ficha manda leer el jsonl.
  Lleva control positivo (una lectura deliberada que el espía tiene que ver); sin él, «cero
  aperturas» podría ser un falso negativo del instrumento.
- `[GUARDA] H3-FAIL-CLOSED-UUID-NUNCA-VISTO` y `[GUARDA] H3-FAIL-CLOSED-DIAG-AMBIGUO` — hoy
  son verdes **trivialmente** (todo levanta), pero pasan a discriminar en cuanto el sobre
  existe: son los dos que impiden convertir el sobre en «devuelvo dict para todo».
- `[GUARDA] H3-RELECTURA-POST-EJECUCION-INTACTA` — fusionar la relectura post-ejecución con el
  snapshot pre-dispatch deja un stop **exitoso** publicado como fallido.

## §Checks retirados o UNMET, con su razón

Ningún check se retiró. Los **6 UNMET** son checks cuyo **sujeto todavía no existe**, y su
aserción sobre un sujeto ausente sería vacua (verdadera por vacío), no falsa:

| Check | Por qué UNMET y no FAIL |
| --- | --- |
| `H2-SIN-TIMESTAMP-NI-PATH` | No hay ningún diagnóstico que inspeccionar. «Ninguna clave prohibida» sobre un conjunto vacío es cierto por vacuidad. El rojo que manda hoy es `H2-DIAG-TRAS-STOP`. |
| `H3-MUT-RECOMPUTA-GENERATION-CHANGED` | No hay sobre. El rojo que manda es `H3-RUN-NOT-ACTIVE-SOBRE`, sobre el mismo fixture. |
| `H3-INTEGRACION-STATUS-REAL` | La guarda anti-vacuidad se dispara: el status real no trae ni el run ausente ni el diagnóstico, así que el encaje M17→M19 no es observable. |
| `H4-DIAGNOSTICOS-SOLO-EN-STATUS` | No hay sobre que inspeccionar. El rojo que manda es `H4-TOOL-TRANSPORTA-EL-SOBRE`. |
| `H5-STOP-EXITOSO-SE-DISTINGUE` | No hay diagnóstico. |
| `H5-REAP-ACREDITA-SIN-TERMINAR` | El fixture sí reapea (`reaped=[…]`), pero no queda diagnóstico que leer. |

`H1-FAIL-CLOSED-SIN-GENERACION` lleva una **guarda anti-vacuidad hacia adelante**: si algún día
el status publica los tres campos pero el manifiesto no llevaba ninguno que quitar, el fixture
legacy sería idéntico al actual y el check pasa a UNMET en vez de a un verde que no mide nada.

## §Gates

```gates
[ ] G1: el sobre de dayz_test_stop, los diagnosticos de runs retirados y las tres generaciones
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los modulos de test que cubren lifecycle, tool y servidor siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

## Notas que evitan gates imposibles, todas medidas

- **La venv NO tiene pytest** (ni `fastmcp`: `mcp` sí). La suite es `unittest`.
- **G2 exige el layout de workspace `<ws>/{addon,tools,gate}`.** Contra una copia pelada de
  `tools/`, `test_process_lifecycle` y `test_loopback` salen rojos por ficheros **no-python**
  ausentes, no por código: `tools/process-guard.ps1` y `addon/scripts/5_Mission/MCPBridge.c`
  (`tests/_addon_paths.py` los busca dos niveles por encima de `tests/`). Medido: en la copia
  congelada salen `FAILED (errors=1)` y `FAILED (errors=2)`; con esos dos ficheros presentes,
  los 11 módulos salen `OK`.
- **`lifecycle.status(client)` publica `dataclasses.asdict(run)` de TODOS los runs**, EXITED
  incluidos (`process_lifecycle.py:2530-2541`). Un run parado **sigue presente** hasta que algo
  recarga el manifiesto. La poda de EXITED ocurre en `RunManifestStore.__init__`
  (`:452,517-539`), y el único camino que la dispara **en vida del daemon** es
  `repair_manifest_recovery`, que rebindea `self.manifest = RunManifestStore(paths)`
  (`:2320-2323`). El oráculo reproduce exactamente ese rebind para fabricar un run ausente con
  el anillo intacto. **Consecuencia de diseño**: el sobre `run_not_found` sólo puede existir
  para un run podado dentro de la vida del daemon que lo retiró; tras un reinicio, el anillo
  está vacío y la respuesta correcta es la excepción pelada.
- **El sobre va en `execute_dayz_test_stop`, no en `resolve_stop_run`.** `resolve_stop_run` es
  API pública con tests propios que exigen que levante (`tests/test_dayz_test_tool.py:357-370`).
  Medido con el prototipo: capturar `DayzTestToolError` en `execute_dayz_test_stop` deja los 11
  módulos acotados en verde; mover la clasificación dentro de `resolve_stop_run` rompería esos
  tests.
- **`_exact_run` valida UUID4 canónico antes de mirar el manifiesto**
  (`dayz_test_tool.py:206-211`): un `run_id` no-UUID sale `bad_run_id`, no `run_not_found`, y
  esa precedencia no se toca.
- **El campo nuevo de `RunRecord` va al final, con default.** Medido: la única construcción
  posicional (`process_lifecycle.py:1445-1462`) pasa 12 argumentos; insertarlo antes de
  `launch_operation_id` desplaza los tres últimos y `launch_acknowledged` recibe un sha **sin
  error de tipo**.
- **Este lote es data-crítico** (`D3=true` en el sentido de DZ-R9: toca el manifiesto de runs y
  el estado del lifecycle). Además del gate, pasa por una auditoría `rigorous-data-audit` con
  auditores por ángulo antes de declararse release-safe. Está decidido por el humano y no es
  negociable desde aquí.
- **El árbol `draft/proto/` NO viaja.** Es la prueba de alcanzabilidad de la regla 7 (ver
  `CALIBRACION.md` y `draft/proto/NO-VIAJA.txt`). Lo que se entrega es: `GATES-lote-H.md`,
  `oracle.py`, `run.sh`, `suite.sh`, `CALIBRACION.md`.

---

## Ronda 2 — rebase sobre la G final, y el anillo sólo afirma lo que el manifiesto confirmó

Escrito el 2026-09-04 tras recibir la ronda 1 (29/29 y suite de 11 módulos verdes corridos por
el orquestador; rojo-antes de sus 17 tests verificado por el orquestador) y **dos revisiones
ciegas entre sí**: Codex (5 MAYOR) y Opus (2 ALTA, 1 MEDIA, 2 BAJA), con solape en lo
estructural. Todas reproducidas por los revisores; las sondas de Opus ejecutadas también por el
orquestador (`review1-opus/probes/`: `P2B` 32 legítimos → 0 supervivientes, `P2C`, `P2D`, `P4/3`).

### Lo que la ronda 1 hizo bien, y se conserva

D2 aguantó los cuatro ataques con `runs.json` reales en los dos sentidos (el árbol anterior
carga el manifiesto nuevo ignorando la clave extra). El sobre aguantó forma del `run_id`,
doble diagnóstico, un solo snapshot pre-dispatch y copia-sin-recalcular. `stop_run` tiene el
orden correcto (persistir → diagnóstico). El diff fue puramente aditivo.

### Cinco decisiones, una por hallazgo

1. **El anillo sólo afirma lo que el manifiesto confirmó.** Cinco de los seis caminos metían
   el diagnóstico ANTES de `manifest.replace`; una retirada fallida quedaba publicada como
   hecha sobre un run presente, y **el reaper (cada 30 s) desalojaba el anillo entero en
   16 min** con copias del mismo run (el sobre `run_not_found` de un run legítimo desaparecía
   sin que nadie lo tocara). Regla: `_retire_run_diagnostic` SÓLO después de que el `replace`
   termine con éxito, en los seis caminos; un fallo de persistencia no consume posición.
2. **El diagnóstico se valida antes del sobre.** `_copy_generation` fabricaba tres `null`
   desde un diagnóstico incompleto (el prototipo del redactor validaba presencia; la entrega
   lo quitó: regresión del fail-closed). Regla: el sobre sólo se construye desde un diagnóstico
   con las OCHO claves exactas, `run_id` exacto, `state == "EXITED"` y tipos válidos; cualquier
   desviación conserva `ToolError("run_not_found")` pelado.
3. **`repair_recovery_fault` rechaza estados terminales y el anillo deduplica.** Repetido
   sobre un run ya `EXITED` devolvía éxito y añadía un segundo diagnóstico → el sobre caía a
   `ToolError` por cardinalidad 2. Regla: rechazo de terminal en la precondición, y un mismo
   `(run_id, launch_operation_id)` no entra dos veces.
4. **Lectura rasgada: una fila NO terminal y un diagnóstico del mismo run no conviven en un
   payload.** `status()` leía el manifiesto y el anillo en instantes distintos; un reap entre
   ambos publicaba el run `RUNNING_IDLE` y su `run_reaped/EXITED` a la vez. NO se toma
   `_operation_lock` en la lectura (bloquearía `/status`, que es el discriminador de salud del
   daemon, durante un stop): sello de revisión antes y después con reintento acotado, y como
   última defensa el filtro «diagnóstico de un run cuya fila del mismo payload no es terminal
   no se publica». La convivencia fila `EXITED` + diagnóstico es la situación NORMAL tras un
   stop (la ficha la exige: el sobre `run_not_active` copia los campos del diagnóstico), y
   `public_status()` ya filtra a activos. Corrección del orquestador: su primera instrucción al
   redactor del gate prohibía toda convivencia; la ficha manda lo contrario.
5. **`reason` es un código cerrado.** `admin_reconcile` copiaba el texto libre del operador
   (paths, timestamps) al diagnóstico público. Regla: `reason="admin_reconciled"`; el texto
   humano se queda en el audit, que D1 impide leer desde status.

### Y el rebase

La ronda 9 del lote G (final: `_fenced_runs`, `_quiesce_then_release_owner`, `_compensating`)
cambió `process_lifecycle.py`, `loopback.py` y dos tests que este lote también toca. El
workspace lleva ya `loopback.py` y `test_loopback.py` de la 9 (este lote no los tocaba) y los
tres ficheros en conflicto como hermanos `.r9`. El worker re-aplica SU diff sobre la base `.r9`
y borra los hermanos. Doble gate: el de este lote (29 + los checks nuevos) y el oráculo de G
(35 checks, `gate-extra/oracle_lote_g.py`, hash verificado al recibir).

### Estado medido antes de delegar

```
ORACULO: PASS=31 FAIL=9 UNMET=0 de 40   (9 rojos: H6 x2, H7 x3, H8, H9, H10 x2; H7-PERSISTENCIA-OK y H9-CONVIVENCIA verdes por diseno; los 29 de la ronda 1 verdes)
```

```gates
[ ] G1: el sobre de stop y los diagnosticos de runs retirados
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los once modulos acotados siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 3 — la ronda 10 del lote G, ejecutada sobre el árbol fusionado

Escrito el 2026-09-04 tras recibir la ronda 2 (rebase sobre la G r9 + los cinco arreglos:
sello 4/4, write-set limpio, **oráculo H 40/40 y suite de 11 módulos OK corridos por el
orquestador, y el oráculo de G en verde sobre el árbol fusionado**; sin disputas). Este
workspace es desde ahora el árbol de integración: G r9 + H r2.

### Por qué la ronda 10 de G corre aquí

La revisión de Codex sobre la G r9 dejó 4 hallazgos (`lote-G/gates_ronda10.md`): el cerco se
ató a los call-sites y no a la transición (`repair_manifest_recovery` → `recover_after_restart`
sin cerco; el poll no revalida al retomar el lock), la compensación no oculta el sello existente
al lector, `retire_run` no borra el cerco, `exec_enforce` colapsa el motivo. Ejecutarla sobre el
workspace de G obligaría a un segundo rebase de H; y el workspace de G lo está leyendo el R9 en
curso (líneas citadas: no se toca). Así que la ronda 10 de G es la ronda 3 de H, con el brief de
G (`lote-G/runs10/brief.txt`) adaptado en rutas y gates.

### Doble gate

- `gate/` (sellado en `runs3/GATE-SEAL.txt`): el oráculo de H, 40 checks, tiene que seguir 40/40.
- `gate-extra/oracle_lote_g.py`: el oráculo de G, 38 checks, SHA-256 `4c562895b057a970…`
  (idéntico a `lote-G/ws/gate/oracle.py` de la ronda 10); verificado por hash al recibir.

### Estado medido antes de delegar, sobre el árbol fusionado

```
gate/run.sh (H):         ORACULO: PASS=40 FAIL=0 UNMET=0 de 40
gate-extra (G, 38):      ORACULO: PASS=35 FAIL=3 UNMET=0 de 38
                         rojos: N26-POLL-REVALIDA, N28-CERCO-NO-SE-ACUMULA, N29-EXEC-ENFORCE-RUN-NOT-OWNED
gate/suite.sh:           SUITE-ACOTADA OK
```

```gates
[ ] G1: el sobre de stop y los diagnosticos de runs retirados
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los once modulos acotados siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 4 — cierre: lo que el R9 verificado añadió

Escrito el 2026-09-04 con el R9 de G **verificado** (Opus como verificador: 18 afirmaciones de
7 ángulos, 14 confirmadas, 4 refutadas, 0 sin verificar; cruzado 6; registro completo en
`lote-G/runs10/R9-VERIFICADO.md`). Es la última ronda de producto de los lotes G y H antes de la
revisión final de Codex, la suite completa y la integración.

### Lo que entra, y por qué

Defectos confirmados por cita e inferencia, con check en el gate de G (ampliado a 42):
- **N30** (R9 #1, P2): el crédito de actividad se muestrea fuera del lock del loopback y después
  de publicar; un release en medio cerca y descarta el comando, y el crédito aterriza igual: un
  run `RUNNING_IDLE` publica `recent` por un comando descartado. Regla: el crédito se ACEPTA bajo
  el mismo lock en que se acepta el comando (epoch muestreado ahí), o el cerco/retirada eleva
  tumba; en cualquier caso un comando descartado no acredita nada.
- **N31** (R9 #2 y cruzado #1, P2): `_retire_instance_locked` descarta la cola sin
  `_flush_queue_discards` (ni `exec_audit 'discarded'` ni cierre de operaciones del
  coordinador; el pin vive hasta el timeout). Regla: retirar cierra lo que descarta, igual que
  `fence_runs`.
- **N32** (R9 #13): `_enqueue_run_rejection` abre paso y acredita cuando `manifest.get` lanza.
  Regla (G6): estado durable ilegible ⇒ rechazo fail-closed, sin crédito.
- **N34** (R9 #12): retirar el binding borra la valla de resultado de los comandos ya
  despachados; un `store_result` sin instancia se acepta como genuino. Regla: la valla sobrevive
  a la retirada para los ids despachados; sin instancia acreditada no hay resultado.

Sin check en el gate (tests del implementador con rojo-antes; los cierro yo al recibir):
- R9 #5: un rollback durable FALLIDO deja vivo el crédito de la ventana del intento (sin olvido
  ni tumba): la compensación corre también cuando el `replace` del rollback falla.
- R9 #4: `begin_release_owner` arma el fallo de recovery con el hash del registro NO
  persistido cuando `replace` falla: el run queda `RUNNING` con dueño muerto e irreparable.
  Regla: el hash armado es el del registro durable, y el run queda reparable.
- Cruzado #6: `admin_reconcile` deja vivo un run no acreditado (STARTING → RUNNING_IDLE) que el
  siguiente arranque del daemon convierte en fault global. Regla: reconcile no produce runs
  que la recuperación no pueda acreditar.
- Cruzado #2 (P2): el hint público de `run_not_owned` ordena una acción con efectos
  (`dayz_test_run mode=client run_id=...` retira el binding vivo y lanza un segundo cliente).
  Regla: el hint nombra la adopción sin efectos, o nada que ejecute.
- R9 #9 y #10: hints incoherentes (`stop it` sobre STARTING/STOPPING que `dayz_test_stop`
  rechaza; `retry with wait_for_box_s` sobre UNRECONCILED con proceso vivo).
- R9 #6 / cruzado #4: una extensión (cliente sobre run existente) no refresca el basal y la
  caja publica `stale` segundos después de lanzar. Regla: el basal de un proceso lanzado por
  este daemon es un sello más (max), no se descarta por existir otro.
- R9 #14: residuos por `run_id` (`_last_activity`, tumbas, `_fenced_runs`) se limpian en la
  salida durable a EXITED.

Fuera, con motivo: R9 #3/#8 (el crédito invalida el caché de sondas: perf; se mide antes de
tocar), R9 #11 (etiqueta `foreign` en «ocupada sin filas»: contrato público, backlog).

### Estado medido antes de delegar

```
gate/run.sh (H): ORACULO: PASS=40 FAIL=0 UNMET=0 de 40 · gate-extra (G, 42 checks, SHA-256 4c48642a5a2fea0a…): ORACULO: PASS=38 FAIL=4 UNMET=0 de 42 — rojos N30, N31, N32, N34 · gate/suite.sh: SUITE-ACOTADA OK
```

```gates
[ ] G1: el sobre de stop y los diagnosticos de runs retirados
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los once modulos acotados siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 5 — cierre 2: la composición G×H, corregida

Escrito el 2026-09-04 tras la revisión final ciega de Codex sobre el producto fusionado
(rondas 4 de H / 11 de G): **3 hallazgos con repro, ninguna familia nueva**, y los tres nacen de
la composición de los dos lotes — exactamente lo que se le pidió mirar al revisor.

- **H-01 ALTA — el método único del anillo invirtió el P4 de G.** `_commit_retirement`
  persiste `EXITED` y SOLO DESPUÉS retira el binding, en cinco de seis caminos; en la ventana,
  un `store_result` tardío para un comando ya despachado se acepta como genuino (la base de G
  retiraba antes: `ws-frozen-r8:2566`). Es una validación debilitada por la composición: el
  helper de H unificó cinco call-sites y cambió el orden que G mantenía. Regla P19: retirar
  (idempotente) → persistir → si persistió, diagnóstico y residuos; si no, binding retirado y
  sin diagnóstico.
- **H-02 MEDIA — la limpieza terminal borró la frontera.** `_forget_run_residues` (P18) borra
  también la tumba; un escritor que aceptó antes de la retirada aterriza después sobre un run
  EXITED. Y `_tombstone_run_activity` absorbe excepciones: si elevar la tumba lanza, la
  transición a `RUNNING_IDLE` sigue fail-open. Reglas P20 y P21.
- **H-03 MEDIA — `None` como permiso.** `_durable_run_state` devuelve `None` para un registro
  sin `state: str` o sin getter, y el consumidor lo trata como autorización: rama fail-open
  preexistente, hermana de la que N32 cerró. Regla P22.

Gate de G ampliado a **45 checks**: N35 (result tardío durante la retirada terminal, barrera
tras persistir), N36 (crédito viejo no reaparece tras la limpieza; caja blanca declarada), N37
(registro malformado no autoriza).

### Estado medido antes de delegar

```
gate/run.sh (H): ORACULO: PASS=40 FAIL=0 UNMET=0 de 40 · gate-extra (G, 45 checks, SHA-256 9c4b482d8e4c2eac…): ORACULO: PASS=42 FAIL=3 UNMET=0 de 45 — rojos N35, N36, N37 · gate/suite.sh: SUITE-ACOTADA OK (456 tests)
```

```gates
[ ] G1: el sobre de stop y los diagnosticos de runs retirados
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los once modulos acotados siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

### Notas de la revisión final de Opus (APROBADO con dos BAJA), incorporadas

- **H-1 BAJA**: los pasos post-persist de `_commit_retirement` no están aislados; con el orden nuevo (P19) los post-persist son diagnóstico y residuos: no lanzan (best-effort, nunca dejan el cerco levantado). Va en el brief como parte de P19.
- **H-2 BAJA, y es una corrección a ESTE ledger**: dije que `/status` no espera a un stop porque la lectura no toma `_operation_lock`. Cierto, pero incompleto: se bloquea ~0,7 s tras `RunManifestStore._lock` durante el `atomic_write_bytes` de una persistencia. Un negativo acotado no es un negativo general (LL). Queda declarado como residuo medido, no como garantía.

---

## Ronda 6 — cierre 3: las tres continuaciones de la revisión delta

Escrito el 2026-09-04 tras la revisión delta de Codex sobre el cierre 2: **1 ALTA + 2 MEDIA,
ninguna familia nueva, las tres continuación de sus tres anteriores.** El cierre 2 sí cerró lo
que decía (orden retirar→persistir→publicar en los seis caminos; P21 sin ruta fail-open; locks
sin inversión; sin validaciones desaparecidas), y dejó tres bordes:

- **ALTA — `lifecycle is None` seguía siendo permiso** (P22 sólo endureció «getter lanza» y
  «registro malformado»). Decisión: un binding BOUND sin lifecycle no despacha (enqueue y
  exec_enforce `run_state_unavailable`, poll retiene). Medido que ningún módulo de producción
  acuña bindings sin lifecycle (los acuña el lifecycle al arrancar): ningún modo real pierde
  nada; la cola legacy sin binding sigue como compatibilidad.
- **MEDIA — la rama `not ok` del escritor no comparaba con la frontera terminal**: un audit
  fallido tras la limpieza recreaba sticky `unknown`. Decisión: la frontera vale para el fallo.
- **MEDIA — adoptar tras una persistencia terminal fallida daba `ok` sobre un binding
  retirado** (run adoptable y mudo). Decisión: rechazo que dirige al reap/recovery, o
  reconstrucción acreditada; nunca restaurar el binding a ciegas.

Gate de G a **48 checks**: N38 (BOUND sin lifecycle no encola ni entrega), N39 (audit fallido
tras la frontera no recrea `unknown`; caja blanca declarada), N40 (adoptar tras persistencia
fallida no entrega un run mudo).

### Estado medido antes de delegar

```
gate/run.sh (H): ORACULO: PASS=40 FAIL=0 UNMET=0 de 40 · gate-extra (G, 48 checks, SHA-256 977ab3677fb6aa77…): ORACULO: PASS=45 FAIL=3 UNMET=0 de 48 — rojos N38, N39, N40 · gate/suite.sh: SUITE-ACOTADA OK
```

```gates
[ ] G1: el sobre de stop y los diagnosticos de runs retirados
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los once modulos acotados siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 7 — cierre 4: la memoria no es evidencia

Escrito el 2026-09-04 tras la segunda revisión delta de Codex sobre el cierre 3: **1 MEDIA,
ninguna familia nueva** (continuación de P19'/H-01). P22' y P20' quedaron cerrados; P19' se
reabre al reiniciar el daemon: el rechazo `binding_retired_pending_reap` se apoyaba en
`ServerState._retired_run_ids`, un `set` en memoria que empieza vacío en cada daemon —justo
la memoria que se pierde en el único momento en que hacía falta (persistencia terminal
fallida)— y que además crecía sin límite (nunca se limpiaba tras una persistencia terminal
que sí aterrizaba).

Cuatro cierres del mismo hallazgo (H-01 → P19 → P19' → hoy) cayendo por ataques distintos:
LL-421 dice que la pregunta estaba mal. La pregunta correcta no es «¿se retiró el binding?»
(memoria del pasado) sino «¿qué acredita HOY el estado durable?»:

- **P19'' [DESIGN] — adoptar exige un proceso vivo y propio.** `adopt_run` particiona los
  procesos registrados con el guard (identidad del manifiesto: pid + creación + hashes): sin
  ningún `owned` → rechazo `run_processes_gone` (409) con `hint` que dirige al reap
  (`reap_dead_runs`) / recovery. Durable: sobrevive al reinicio porque no depende de nada en
  memoria. Un run con procesos `gone`/`foreign` junto a uno `owned` sigue adoptable
  (preservación de `test_adopt_allows_absent_registered_process`).
- **La marca volátil desaparece.** `_retired_run_ids`, `run_bindings_retired` y
  `_bindings_retired_pending_reap` se retiran junto con el código `binding_retired_pending_reap`
  y su hint: una memoria que miente tras el reinicio y crece por run es peor que ninguna. El
  test `test_adopt_after_failed_terminal_persist_directs_to_reap` pasa a esperar
  `run_processes_gone`.
- **Adoptar declara la despachabilidad.** Los bindings no sobreviven al daemon
  (`unbound_after_restart`): tras un reinicio un run VIVO sigue adoptable —parar exige
  adoptar (`run_not_adopted`)— pero no despacha una sola mutación. La respuesta `ok` de
  `adopt_run` lleva `dispatchable: <bool>` calculado del `ServerState` PRESENTE (algún
  binding del run en estado BOUND); con `False`, un `hint` que diga que los bindings no
  sobreviven al reinicio y que el run admite `stop_run` y relanzar. Con `bindings is None`
  (sin ServerState cableado) → `dispatchable: False`, fail-closed.

Gate de G a **51 checks**: N41 (persistencia fallida + reinicio real —lifecycle y ServerState
nuevos sobre el mismo manifiesto y guard— adoptar no entrega un run muerto), N42 (reinicio
con proceso vivo: un ok sin `dispatchable: False` sobre un run que rechaza `world_spawn` es
un run mudo sin declarar), N43 (preservación: adoptar-para-parar sobrevive al reinicio; verde
hoy, guarda contra el rechazo indiscriminado).

### Estado medido antes de delegar

```
gate/run.sh (H): ORACULO: PASS=40 FAIL=0 UNMET=0 de 40 · gate-extra (G, 51 checks, SHA-256 461e315dda0ecf89…): ORACULO: PASS=49 FAIL=2 UNMET=0 de 51 — rojos N41, N42 · gate/suite.sh: SUITE-ACOTADA OK
```

```gates
[ ] G1: el sobre de stop y los diagnosticos de runs retirados
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los once modulos acotados siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```
