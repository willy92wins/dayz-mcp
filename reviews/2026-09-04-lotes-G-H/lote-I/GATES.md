# GATES — lote I (reconciliación de tests externos con el contrato de G y H)

El producto `dayz_mcp/` está congelado por hash; el único artefacto de este lote son los tests.
## Ronda 1 — lote I: reconciliación de los tests externos con el contrato de los lotes G y H

Escrito el 2026-09-04 tras integrar el producto de G r14 + H r7 en el árbol del repo y correr la
suite COMPLETA por primera vez sobre él: **2.524 tests, 30 rojos nuevos** (la misma suite sobre un
worktree limpio de HEAD `00d4303` da 2.193 tests y sólo los 2 fallos conocidos). Los 30 viven en
cuatro módulos que el gate acotado de once módulos no cubría —`test_instance_fence` (12),
`test_client_mode` (14), `test_daemon` (1) y `test_session_e2e` (5, fichero sin trackear desde
el 19-08)— y todos son la misma familia: **fixtures que presentan estados imposibles en
producción bajo el contrato nuevo**, no defectos del producto:

- Peers acreditados con `install_bound_peer`/`bind_both_peers` para un `run_id` (`test-run`,
  `testrun`) que el manifiesto del lifecycle real NO contiene → `binding_retired` (un run que no
  existe en el manifiesto es un run retirado; P1/P22'). En producción todo binding lo acuña
  `start_run` sobre un run del manifiesto.
- `ServerState` con el lifecycle cableado en una sola dirección (`bindings=state` sin
  `state.lifecycle = lifecycle`) o con un lifecycle falso sin `manifest.get` →
  `run_state_unavailable` 503 (P22': sin estado durable legible no hay despacho). El daemon
  cablea las dos direcciones (`daemon.py:530-536`).
- `test_every_exited_path_retires_bindings` (AST): busca `_retire_run_bindings` en cada función
  que asigna `EXITED`; desde H r5 los seis caminos llaman a `_commit_retirement`, que retira
  ANTES de persistir. La invariante se cumple; el detector no la ve.
- `test_release_owner_retires_bindings`: su aserción codifica el contrato viejo. Desde G r9
  (P6 estricto, N20) el release NO retira el binding: lo cerca (`_fenced_runs`), la mutación
  siguiente es `run_not_owned` y `adopt_run` rehabilita el MISMO binding sin relanzar.

## Producto de este lote

Los cuatro módulos de test reconciliados (más `fence_helpers.py` si el arreglo común lo pide),
con el **producto `dayz_mcp/` congelado por hash**. Reglas:

- **R1 — Fixture antes que aserción.** Si el rojo es un estado imposible, se arregla el fixture
  (un `RunRecord` RUNNING con dueño en el manifiesto real; `state.lifecycle = lifecycle`; o el
  seam `_BoundPeerDispatchable` que ya usa `install_bound_peer` cuando no hay lifecycle) y la
  aserción queda **byte a byte igual**.
- **R2 — Una aserción sólo cambia si un P-item la supersede**, y se lista en STATE.md como
  `test → aserción vieja → aserción nueva → P-item`. Sin P-item, no se toca: DISPUTAS.
- **R3 — Un detector estático se amplía, no se relaja**: `test_every_exited_path_retires_bindings`
  acepta como retirada una llamada a `_commit_retirement` (cuyo cuerpo llama a
  `_retire_run_bindings` antes de `manifest.replace`), y además exige ese orden.
- **R4 — Nada del producto cambia.** Si un test sólo puede ponerse verde tocando `dayz_mcp/`, eso
  es un defecto del producto y va a DISPUTAS con el test exacto, no al diff.
- **R5 — `test_session_e2e.py` está sin trackear**: se reconcilia igual (el árbol de trabajo es lo
  que ven las demás sesiones) y NO se commitea; STATE.md lo dice.

### Estado medido antes de delegar

```
gate/product_frozen.sh: PRODUCTO-CONGELADO OK (63 ficheros) · gate/suite_full.sh: SUITE-COMPLETA ROJA — 30 rojos no permitidos, 0 permitidos que ya no fallan (Ran 2524 tests)
```

```gates
[ ] I1: el producto no se ha tocado
  CHECK: gate/product_frozen.sh
  EXPECT: PRODUCTO-CONGELADO OK
  EVIDENCE: pending

[ ] I2: la suite completa por NOMBRE es exactamente la baseline (2 fallos conocidos)
  CHECK: gate/suite_full.sh
  EXPECT: SUITE-COMPLETA OK
  EVIDENCE: pending
```

---

## Ronda 2 — los cuatro hallazgos de la revisión ciega de Codex sobre la ronda 1

Escrito el 2026-09-04. La ronda 1 dejó el producto congelado, la suite completa verde (tres pasadas
mías: Ran 2524, rojos = los 2 permitidos) y sólo dos ficheros tocados. Codex, revisando los TESTS
como producto, encontró **2 ALTA + 1 MEDIA + 1 BAJA**, todos reproducidos con controles negativos
sobre copias temporales del producto:

- **F-01 ALTA — el helper inventa un dueño ajeno al coordinador.** `bind_both_peers` registra en
  el manifiesto real un run RUNNING con `fence-fixture-owner`/`fence-fixture-lease` y pids
  sintéticos que el guard real clasifica `process_not_found`. Consecuencia medida: un e2e libera
  a A y B muta sin adoptar, y pasa sólo porque el run pertenece al dueño sintético (liberar A no
  lo cerca). Con el dueño acoplado a A el test muere en `remote_error`. Un fixture que esconde C2.
- **F-02 ALTA — el detector AST agrega por función.** «Alguna llamada retira en algún sitio de la
  función» no es «todo camino desde cada `EXITED` retira antes de persistir o retornar». Un
  mutante que añade a `admin_reconcile` una rama que asigna EXITED, persiste y retorna sin retirar
  sobrevive; el mutante de orden invertido sí muere.
- **F-03 MEDIA — el test «without_lifecycle» ya no ejecuta la rama `lifecycle is None`.** El helper
  instala el fake antes del poll; sustituir la rama por `raise` deja el test verde.
- **F-04 BAJA — `test_release_owner_retires_bindings` afirma lo contrario de su cuerpo.**

Su gate completo salió rojo en el sandbox de Codex (48 ERROR de path authority / launcher nativo /
`psutil.AccessDenied`), no atribuible al diff; en este entorno tres pasadas verdes. Se registra.

### Producto de esta ronda

- **P-I1 — el dueño lo pone el test, nunca el helper.** Con un lifecycle real, `bind_both_peers`
  registra el run RUNNING sólo con un dueño que el test controla: el titular ACTIVO del lease
  `lifecycle` del coordinador (`session_id` + `lease_id` reales) o un `owner=(session_id, lease_id)`
  explícito; sin ninguno de los dos, NO registra un run con dueño inventado (deja el manifiesto
  como estaba, o lanza). Los procesos registrados los clasifica `owned` el guard del lifecycle
  —con guard falso, sembrando sus snapshots con la identidad completa; con guard real, un proceso
  vivo de verdad (el propio intérprete) con la identidad que el guard devuelve—.
- **P-I2 — el e2e que libera A y sigue con B adopta con B** antes de la mutación siguiente
  (C2/C4); con el dueño acoplado a A ese paso deja de ser opcional.
- **P-I3 — detector sensible al camino.** Para cada asignación `state = "EXITED"`: en el bloque que
  la contiene y en los que lo encierran, ningún `return` ni `manifest.replace` alcanzable después de
  la asignación precede a una llamada que retira (directa o a una función del módulo que retira
  ANTES de persistir). El mutante bypass y el de orden invertido son controles rojos permanentes
  del propio test (mutantes sobre copia temporal, nunca sobre el workspace).
- **P-I4 — «without_lifecycle» ejecuta su rama:** el enqueue usa el fake que exige C1 y
  `state.lifecycle = None` va inmediatamente antes de `record_poll`; o se renombra a
  «without_guard» y se añade un test aparte que sí recorre la rama `lifecycle is None`.
- **P-I5 — el nombre dice lo que el cuerpo prueba:** `test_release_owner_fences_bound_binding_until_adopt`.

### Oráculo de tests (`gate/oracle_tests.py`, corrido por `gate/run_oracle_tests.sh`)

  T1a-DUENO-ACOPLADO: con A titular del lease, el run que registra el helper tiene dueño (A, lease de A). ROJO hoy.
  T1b-PROCESOS-OWNED-GUARD-REAL: con `NativeProcessGuard`, todos los procesos registrados clasifican `owned`. ROJO hoy.
  T1c-SIN-LEASE-NO-INVENTA-DUENO: sin lease activo ni `owner`, el manifiesto no acaba con un run RUNNING de dueño desconocido. ROJO hoy.
  T2-MUTANTE-BYPASS-EXITED: el detector muere con la rama bypass en `admin_reconcile`. ROJO hoy.
  T2b-MUTANTE-ORDEN-INVERTIDO: el detector muere con retire↔replace invertidos (preservación). Verde hoy.
  T3-MUTANTE-RAMA-SIN-LIFECYCLE: el test «without_lifecycle» muere si la rama `lifecycle is None` lanza. ROJO hoy.
  T4-NOMBRE-RELEASE: no existe `test_release_owner_retires_bindings`; existe el nombre de P-I5. ROJO hoy.

### Estado medido antes de delegar

```
gate/product_frozen.sh: PRODUCTO-CONGELADO OK (63 ficheros) · gate/run_oracle_tests.sh: ORACULO-TESTS: PASS=1 FAIL=6 UNMET=0 de 7 — rojos T1a, T1b, T1c, T2, T3, T4 (T2b verde, controles positivos verdes) · gate/suite_full.sh: SUITE-COMPLETA OK (Ran 2524, rojos = los 2 permitidos)
```

```gates
[ ] I3: el oraculo de tests (mutantes y acoplamiento del dueno)
  CHECK: gate/run_oracle_tests.sh
  EXPECT: ORACULO-TESTS-VERDE
  EVIDENCE: pending
```

---

## Ronda 3 — los tres hallazgos de la delta de Codex sobre la ronda 2

Escrito el 2026-09-04. La ronda 2 cerró F-01..F-04 (oráculo 7/7, producto congelado, suite verde).
La delta de Codex sobre ese diff de tests dio **2 ALTA + 1 MEDIA**, reproducidos con mutantes:

- **F-05 ALTA — un lease caducado se convierte en dueño RUNNING.** El helper resolvía el titular
  leyendo `coordinator._active` en crudo, sin el lock y sin `_expire_due()`: con el reloj
  avanzado más allá de `SESSION_TTL_S`, el manifiesto quedaba RUNNING con A. P-I1 exige el titular
  ACTIVO: el que devuelve una operación del coordinador que expira bajo su lock.
- **F-06 ALTA — el detector colapsa las ramas de un `if`.** Una rama que retira tapa a la rama
  que persiste EXITED y retorna sin retirar (mutante split-branch en `admin_reconcile`, distinto
  del bypass y del orden). Sigue siendo pertenencia agregada, no sensibilidad a todos los caminos.
- **F-07 MEDIA — el helper de adopt del e2e no observa `dispatchable`.** Un mutante que publica
  `dispatchable: False` sin volver a cercar deja el e2e verde.

Backlog de Codex, aceptado: el `owner=` explícito no se contrasta con el coordinador (hoy sólo lo
usa `test_daemon.py`, sin secuencia release→mutación); se limita y se documenta. Su suite completa
volvió a salir roja en su sandbox (47 ERROR + 1 FAIL `psutil.AccessDenied`); aquí verde.

### Producto de esta ronda

- **P-I6 — el titular activo lo dice el coordinador, bajo su lock.** El helper resuelve el dueño con
  una operación pública del coordinador que expira antes de responder (`status(client)` u otra que
  llame a `_expire_due()` bajo `_condition`), nunca leyendo `_active`. Con el lease vencido no hay
  dueño: el run queda RUNNING_IDLE sin owner o sin registrar. Control permanente: la reproducción
  con reloj controlado (T5).
- **P-I7 — el detector propaga conjuntos de salidas.** Cada sentencia produce el conjunto de
  resultados de sus salidas alcanzables (`fall`, `retire`, `bad`); un `if` es la UNIÓN de sus
  ramas; una secuencia compone por rama; una rama `retire` no borra una rama `fall`. El mutante
  split-branch pasa a ser el tercer control permanente del propio test.
- **P-I8 — el helper de adopt del e2e afirma `result.get("dispatchable") is True`** justo después
  del `ok`; la mutación posterior queda como prueba independiente del efecto.
- **P-I9 — `owner=` explícito acotado:** o coincide con el titular activo del coordinador cuando
  lo hay (si no coincide, `AssertionError`), o el fixture no libera ni adopta después (documentado
  en el docstring del helper). `test_daemon.py` sigue pudiendo usarlo porque no libera.

### Oráculo (ampliado a 10 checks)

  T5-LEASE-CADUCADO-NO-ES-DUENO: reloj > TTL antes del bind → no hay run RUNNING con dueño. ROJO hoy.
  T6-MUTANTE-RAMA-DIVIDIDA: split-branch en `admin_reconcile` → el detector muere. ROJO hoy.
  T7-MUTANTE-DISPATCHABLE-FALSE: adopt publica False → el e2e que adopta muere. ROJO hoy.

### Estado medido antes de delegar

```
gate/product_frozen.sh: PRODUCTO-CONGELADO OK (63 ficheros) · gate/run_oracle_tests.sh: ORACULO-TESTS: PASS=7 FAIL=3 UNMET=0 de 10 — rojos T5, T6, T7 (controles positivos verdes) · gate/suite_full.sh: SUITE-COMPLETA OK (Ran 2524, rojos = los 2 permitidos)
```

---

## Ronda 4 — ORCHESTRATOR_NEEDED: dos preguntas mal hechas (LL-421)

Escrito el 2026-09-04. La segunda delta de Codex sobre la ronda 3 dio **3 ALTA** y declaró
`ORCHESTRATOR_NEEDED`: F-08 el helper escribe como dueño la sesión PÚBLICA truncada a 12
caracteres (`public_payload`) y el release del titular real no encuentra el run; F-09 el `owner=`
explícito casa por PREFIJO con el titular; F-10 el detector trata `break` como caída (mutante
loop-break sobrevive).

Dos familias han caído a un ataque distinto en cada una de tres rondas:

| pregunta | r1 | r2 | r3 |
|---|---|---|---|
| «¿quién es el dueño?» inferido por el fixture | dueño inventado (F-01) | lease caducado (F-05) | sesión truncada / prefijo (F-08, F-09) |
| «¿todo camino EXITED retira?» por análisis estático | por función (F-02) | ramas de un `if` (F-06) | `break` en bucle (F-10) |

LL-421: la métrica no es el problema; la pregunta no es observable con ese instrumento. Decisión
de orquestador, no otra iteración:

- **P-I10 — el fixture NUNCA escribe un dueño.** `bind_both_peers` pierde el parámetro `owner=` y
  toda inferencia: con un manifiesto real registra el run **RUNNING_IDLE** sin dueño, con procesos
  que el guard clasifica `owned`. La propiedad sólo se obtiene por operaciones del producto
  (`adopt_run` con un lease real, o `start_run`): la identidad del manifiesto es la que el
  lifecycle autorizó, completa e infalsificable por construcción. Los fixtures que necesitan un
  run despachable adquieren un lease con una identidad real y adoptan; los tests que adquieren su
  propio lease adoptan después de adquirir (como ya hace el e2e). Las aserciones que cambien por
  esto se listan con su C-item (P6 estricto: sin dueño no despacha NADA, lecturas incluidas).
- **P-I11 — el detector estático abandona honestamente «todos los caminos».** Lo que sí puede
  probar: el conjunto EXACTO de funciones que asignan `EXITED` (hoy seis:
  `_reap_run_locked`, `_settle_failed_launch`, `begin_release_owner.cleanup`,
  `repair_manifest_recovery`, `repair_recovery_fault`, `stop_run`) y que cada una tiene un test
  de orden EN TIEMPO DE EJECUCIÓN nombrado (`test_persist_failure_on_<camino>_retires_binding_and_
  rejects_late_result`, que ya existen en `test_process_lifecycle.py` para reap, begin_release,
  repair_recovery, manifest_recovery y stop; `_settle_failed_launch` es la excepción declarada). El
  test pasa a llamarse por lo que prueba, su docstring dice lo que NO prueba (un CFG no se
  intenta; un mutante con una rama oculta dentro de una función conocida no es un camino real),
  el mapa `RETIREMENT_TESTED_EXITED_SITES` es la lista que un sitio nuevo tiene que ampliar, y la
  comprobación textual de orden en `_commit_retirement` se conserva como heurística. Los mutantes
  bypass, split-branch y loop-break se RETIRAN del test.
- **Oráculo**: T1a se reescribe (helper → RUNNING_IDLE; `adopt_run(A)` acredita al dueño completo
  y despachable); T9 sesión de más de 12 caracteres: adopt + release casan; T10 sitios EXITED
  enumerados con test runtime nombrado; T11 la firma no tiene `owner`. **ABANDON T2, T6** (mutantes
  de sensibilidad al camino: la dimensión se abandona con esta decisión; quien la observa son los
  tests runtime de cada camino). T2b (orden textual en `_commit_retirement`), T3, T4, T5, T7, T1b,
  T1c se conservan.

### Estado medido antes de delegar

```
gate/product_frozen.sh: PRODUCTO-CONGELADO OK (63 ficheros) · gate/run_oracle_tests.sh: ORACULO-TESTS: PASS=7 FAIL=4 UNMET=0 de 11 — rojos T1a, T9, T10, T11 (T2 y T6 retirados con ABANDON; T2b, T3, T5, T7 verdes con controles positivos) · gate/suite_full.sh: SUITE-COMPLETA OK (Ran 2524, rojos = los 2 permitidos)
```

### Ronda 4 — tras la recepción (orquestador)

Recibida: sello 8/8, write-set limpio (+180/−458), producto congelado, oráculo 11/11, suite completa con UN
rojo no permitido —`test_wait_for_marker...test_lookback_can_match_a_line_written_before_the_action`,
intermitente por carga (5/5 verde en solitario), ajeno al diff—. Refinamiento del gate por el autor del
gate, DESPUÉS de recibir y declarado al revisor: los dos enumeradores de sitios EXITED contaban sólo
`ast.Constant` y `admin_reconcile` asigna `"RUNNING_IDLE" if survivors else "EXITED"`; ahora cuentan
cualquier valor que PUEDA ser EXITED (Constant o IfExp) y `admin_reconcile` entra en el mapa con su test
runtime ya existente. Oráculo re-sellado por hash: `aaa46444fbe90ebd…`; T10 verde con siete sitios.
El worker conservó el id viejo del detector como alias para T2b (sellado).

### Cierre del lote I (orquestador, tras la tercera delta de Codex)

Tercera delta: **P-I10 sin hallazgos** (el fixture ya no escribe dueños; cada adopción usa identidad y
lease reales por `adopt_run`/`/lifecycle/adopt`; las aserciones cambiadas, todas con C-item; ningún
negativo tautológico). **P-I11: 1 ALTA** —«exacto» no era cierto: un sitio que asigna EXITED por
variable intermedia, `AnnAssign`, tupla, `setattr` o reemplazo de objeto no entra en el inventario—
y `ORCHESTRATOR_NEEDED` con dos salidas: (a) rebajar el detector a heurística de deletreos conocidos,
(b) una frontera estructural en el producto (una sola operación de transición terminal) que haga el
inventario exhaustivo. Decisión: **(a)** ahora —el test pasa a llamarse
`test_every_literal_exited_site_has_a_runtime_retirement_order_test`, su docstring nombra los
deletreos que NO ve, T10 se rebaja igual y el helper que quedó bajo `unittest.main()` (BAJA) sube—;
**(b)** al backlog como lote de producto. BAJA/backlog de Codex: `release_fixture_owner` sin callers
(se deja); alias del id viejo para T2b (se deja); `10_Projects/DayZ_MCP/bug-ledger.md:138` afirma
todavía «retirada en todo camino» (reconciliar en el vault con el OK de Guillermo). Estos retoques
son del orquestador, siguen la opción (a) del propio revisor y NO tuvieron una cuarta delta.
