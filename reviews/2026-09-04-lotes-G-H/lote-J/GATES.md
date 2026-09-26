# GATES — lote J (la limpieza del daemon sobrevive al cerco; la concesión del lease adopta el run)

Producto de este lote: `tools/dayz_mcp/loopback.py` y `tools/dayz_mcp/process_lifecycle.py` (el resto
de `dayz_mcp/` congelado por hash) + los tests que lo prueban.

## Ronda 1 — dos consecuencias de P6 estricto (lotes G/H, commit `bdeb87f`) medidas en producción

Escrito el 2026-09-04. Tres fichas del buzón describen el mismo contrato desde dos lados:

- `fb-20260904-093000-f7af` (hallazgo propio, lote I): `cleanup_begin` (`daemon.py:77-108`) encola
  `vehicle_release` con `internal=True` (`loopback.py:2702-2704`) y DESPUÉS `begin_release_owner` cerca
  el run (`process_lifecycle.py:1666-1690` → `fence_runs`, `loopback.py:1195-1211`) y `_drain_run_locked`
  (`loopback.py:1154-1170`) descarta la cola entera, la limpieza incluida. `vehicle_release_enqueued`
  vale 1 y el peer client no recibe nada: el vehículo queda bajo control fantasma.
- `fb-20260904-112553-f70b` y `fb-20260904-114825-5ca1` (dos sesiones distintas, mismo día): NINGÚN run
  llega a tener dueño con las tools. `dayz_test_run` lanza con un lease interno y lo suelta →
  RUNNING_IDLE sin dueño; `session_acquire_wait` concede el lease de la caja pero no adopta;
  `adopt_run` (`process_lifecycle.py:2487`) no está expuesto como tool; y P6 estricto cerca todo run
  RUNNING_IDLE (`loopback.py:1272-1287`). Livelock: bloquea la verificación in-game de todos los
  proyectos (medido con `run_not_owned` subiendo +1 por llamada con el puente sano).

### Las dos decisiones (P-J1, P-J2)

- **P-J1 — La limpieza interna del daemon está EXENTA del cerco.** El cerco protege de sesiones sin
  dueño; una orden que el propio daemon encola al liberar (`internal=True`, único llamador
  `cleanup_owner`) se ENTREGA aunque el run esté RUNNING_IDLE/cercado, no acredita actividad y no
  saca al run de RUNNING_IDLE. Todo lo demás sigue drenándose y rechazándose exactamente igual.
- **P-J2 — La CONCESIÓN del lease de la caja adopta el run sin dueño.** Donde el daemon concede un
  lease (`/session/acquire` inmediato y `/session/wait` al llegar el turno de la FIFO) adopta, por
  `adopt_run` y con la identidad y el lease reales del concesionario, el único run RUNNING_IDLE sin
  dueño de la caja, y lo declara en `adopted_run`. El lease se concede igual si no hay nada que
  adoptar (`adopted_run: null`) o si la adopción se rechaza (`{"ok": false, "error": ...}`): el
  motivo viaja en la respuesta. `adopt_run` pasa a ser idempotente para el mismo dueño (misma sesión
  y mismo lease), porque `dayz_test_worker` adopta explícitamente antes de extender o parar
  (`dayz_test_worker.py:527-545`, `:593-596`) y esas rutas no pueden romperse.

### Estado medido antes de delegar

```
gate/product_frozen.sh: PRODUCTO-CONGELADO OK (61 ficheros sellados + 2 del write-set) ·
gate/run_oracle_tests.sh: ORACULO-TESTS: PASS=4 FAIL=9 UNMET=0 de 13 — rojos JA1, JA3, JB1, JB2, JB3, JB4, JB5, JB6, JB8;
verdes JA2 (con control positivo), JA4, JA5, JB7 · gate/suite_full.sh: SUITE-COMPLETA OK (rojos = los 2 permitidos)
```

```gates
[ ] J1: nada del producto fuera de loopback.py y process_lifecycle.py cambia
  CHECK: gate/product_frozen.sh
  EXPECT: PRODUCTO-CONGELADO OK
  EVIDENCE: pending

[ ] J2: los 13 checks del oráculo (P-J1: JA1-JA5; P-J2: JB1-JB8) en verde, controles negativos incluidos
  CHECK: gate/run_oracle_tests.sh
  EXPECT: ORACULO-TESTS-VERDE
  EVIDENCE: pending

[ ] J3: la suite completa por NOMBRE es exactamente la baseline (2 fallos conocidos)
  CHECK: gate/suite_full.sh
  EXPECT: SUITE-COMPLETA OK
  EVIDENCE: pending
```

---

## Ronda 1 — recepción, ruling de la DISPUTA y constructibilidad de JA2 (addendum del orquestador)

Escrito el 2026-09-04 tras recibir la ronda 1 (worker Cursor Grok 4.6 High, 14:24-14:51). Recepción
(`runs1/recepcion.txt`): sello 8/8, write-set dentro del allowlist, tres gates VERDES corridos por el
orquestador (PRODUCTO-CONGELADO OK · ORACULO-TESTS 13/13 · SUITE-COMPLETA OK con los 2 rojos permitidos),
diff LF +476 −19, congelado en `ws-frozen-r1`.

**DISPUTA del implementador, aceptada.** El brief pedía que `_enqueue_run_rejection(internal=True)` no
rechazara RUNNING_IDLE. El test sellado fuera del write-set
`tests.test_box_occupancy.RunCommandActivityTest.test_internal_command_on_idle_run_is_run_not_owned`
(`test_box_occupancy.py:1394-1405`) afirma lo contrario para el API Python con el idle ya publicado.
Ruling: la exención de P-J1 es de ENTREGA (drenaje + poll de ids en `_fire_and_forget_ids`) y de ENCOLADO
sólo durante la ventana del release (run cercado cuyo estado durable sigue RUNNING/STARTING); un
`internal=True` nuevo sobre un idle durable sigue `run_not_owned` porque no tiene productor legítimo
(`cleanup_owner` con lease caducado devuelve `cleanup_fenced` antes de encolar). En producción
`cleanup_begin` (`daemon.py:85-89`) encola ANTES de `begin_release_owner`; Codex confirmó que append y alta
en `_fire_and_forget_ids` van bajo el mismo RLock que poll y drenaje: sin ventana ni doble entrega.

**JA2 sigue siendo construible tras el arreglo** (objeción de la sesión LFQuad2): el check fabrica el run
sin dueño como «dueño que liberó y nadie readoptó» y lanza la lectura SIN pedir lease (caso 8 de f70b);
la adopción sólo ocurre al CONCEDER un lease. Su control positivo (cerco parcheado → 200) prueba que
observa el cerco. JA5 usa la misma construcción.

## Ronda 2 — los tres hallazgos de la revisión ciega de Codex sobre la ronda 1

Dictamen (`review1/REVIEW-CODEX.md`): «NO ES SEGURO INTEGRAR en ronda 1: P-J1 coherente; P-J2 tiene un
fallo ALTO reproducible». Los tres, con repro ejecutado por el revisor:

- **F-01 ALTA** — `_adopt_on_grant` llama a `lifecycle.adopt_run` sin frontera de excepción, DESPUÉS de que
  el coordinador haya concedido el lease; y `adopt_run` abre la reserva (`_authorize`) antes de entrar en
  `_operation_lock` sin cerrarla si una dependencia lanza. Repro: con un run RUNNING_IDLE sin dueño y un
  `manifest.list_runs` que lanza `OSError` en su segunda lectura, `/session/acquire` muere
  (`RemoteDisconnected`) con el lease YA concedido y `pending_authorizations == 1` en el lease activo:
  respuesta cortada, cliente sin saber que tiene lease, reserva huérfana.
- **F-02 MEDIA** — un `list_runs` que falla produce `adopted_run: null`, indistinguible de «no había run»;
  el contrato dice que `null` es sólo enumeración válida sin candidato. Repro: `list_runs` lanzando →
  `200 active adopted_run None`.
- **F-03 BAJA** — `test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published` afirma ahora
  que la limpieza SOBREVIVE y se entrega: el nombre dice lo contrario de la aserción.

Sin hallazgo: integridad de hashes; predicado único de exención y sin acreditación de actividad; carrera
append/alta bajo el mismo RLock; ramas y locks de la adopción (sólo acquire/wait 200, fuera del lock del
coordinador); idempotencia con sesión + lease + RUNNING; flujos del lanzador; transporte de `adopted_run`
por `control_client` sin cambios; +12 tests, ninguno borrado, sin skips. Lo que Codex no pudo verificar:
el historial de `allowed_red.txt` (lo cubre el sello: 8/8 en la recepción) y la suite completa (rojos
ambientales de su sandbox; la suite verde la corrió el orquestador).

### Producto de la ronda 2 (write-set idéntico)

- **F-01**: `adopt_run` es exception-safe tras `_authorize`: toda salida inesperada anterior al commit
  cierra la reserva (abort/rechazo por el coordinador) y devuelve un error declarado; y `_adopt_on_grant`
  envuelve la adopción entera: NUNCA propaga (el lease ya es del llamante), declara
  `{ok:false, run_id, error:"adopt_failed"}` y no deja reserva huérfana.
- **F-02**: `list_runs` ilegible → `{ok:false, run_id:null, error:"run_state_unavailable"}`; `null` sólo
  para una enumeración válida sin candidato.
- **F-03**: renombrar el test a `..._survives_when_idle_published` sin tocar su lógica.

### Estado medido antes de delegar la ronda 2

```
gate/product_frozen.sh: PRODUCTO-CONGELADO OK · gate/run_oracle_tests.sh: ORACULO-TESTS: PASS=13 FAIL=3 UNMET=0 de 16 —
rojos JB9 (manifiesto ilegible → null), JB10 (adopt que revienta corta la concesión), JC1 (nombre del test) · gate/suite_full.sh: SUITE-COMPLETA OK
```

```gates
[ ] J4: los 16 checks del oráculo (13 de la ronda 1 + JB9, JB10, JC1) en verde
  CHECK: gate/run_oracle_tests.sh
  EXPECT: ORACULO-TESTS-VERDE
  EVIDENCE: pending
```

---

## Ronda 3 — dos MEDIA de la revisión delta de Codex sobre la ronda 2, y el tope del bucle

Recepción de la ronda 2 (`runs2/recepcion.txt`): sello 8/8, write-set limpio, 16/16, suite completa verde,
diff +257 −119, congelado en `ws-frozen-r2`. Dictamen delta (`review-delta/REVIEW-CODEX.md`): «NO ES SEGURO
INTEGRAR: el caso ordinario de F-01 quedó corregido, pero…», dos MEDIA con repro ejecutado:

- **F-04 MEDIA (F-01 incompleto)** — `adopt_run` `process_lifecycle.py:2595-2608`: el compensador
  `abort_reservation` va dentro de un `except: pass` y su valor de retorno (tupla de degradaciones,
  `session_coordination.py:1373-1411`, p. ej. `("audit_failed",)`) se tira. Si el compensador lanza, se
  responde `adopt_failed` como si el cierre hubiera ocurrido y la reserva sigue viva (`pending 1`; el
  heartbeat no la limpia). Repro: `manifest.get` lanzando + `abort_reservation` lanzando →
  `200 active {ok:false, error:adopt_failed}` con `pending_authorizations == 1`.
- **F-05 MEDIA (F-02 incompleto)** — `_adopt_on_grant` `loopback.py:3136-3153`: `getattr(run, "state",
  None)` hace tolerante una fila sin esquema; `list_runs() -> [object()]` no lanza, `idle` queda vacío y
  se publica `null` («no había candidato»). Codex admite que `RunManifestStore.list_runs` devuelve clones
  de `RunRecord` (`:740-742`) y no puede producirla por sí solo: es una entrada que el contrato pide
  clasificar, no un fallo espontáneo.
- Sin hallazgo: F-01 camino ordinario (acquire y wait: `adopt_failed`, `pending 0`), rama idempotente,
  F-03, integridad, +3 tests conductuales rojos contra r1 y verdes en r2, ningún skip. BACKLOG cosmético:
  `gate/run_oracle_tests.sh:2` aún dice «lote I» (se corrige al cerrar, fuera del sello).

### Tope del bucle (decisión del orquestador, `gates-ledger` §Cuándo para un bucle)

Tres rondas cayendo a ataques cada vez más profundos sobre la misma pregunta («¿es la adopción
fail-safe bajo fallos de infraestructura?»): adopt lanza → el compensador lanza → filas malformadas.
La ronda 3 es la ÚLTIMA de implementación. Su delta de Codex cierra el lote: CRÍTICA/ALTA con repro →
ronda 4 sólo si el arreglo es local y sin cambio de contrato; MEDIA/BAJA → backlog con el repro archivado,
y se integra. Lo que no se pueda cerrar se declara en el ledger y en las resoluciones, no se reabre.

### Producto de la ronda 3 (write-set idéntico)

- **F-04**: el compensador no se silencia. Las degradaciones que devuelve `abort_reservation` se
  recogen en `cleanup_degraded` del error de `adopt_run` (mismo patrón que `stop_run`,
  `process_lifecycle.py:2280-2296`) y `_adopt_on_grant` las propaga a `adopted_run.cleanup_degraded`.
  Si `abort_reservation` LANZA: intentar el cierre alternativo (`reject_reservation` con motivo
  `adopt_failed`); si también falla, declarar `reservation_abort_failed` en `cleanup_degraded`. Nunca
  una respuesta que afirme un cierre que no ocurrió.
- **F-05**: materializar y validar las filas antes de filtrar: cualquier elemento sin `state`,
  `owner_session_id` o `run_id` lleva la enumeración entera a `run_state_unavailable`; `null` queda
  exclusivamente para una lista válida sin candidato.

### Estado medido antes de delegar la ronda 3

```
gate/product_frozen.sh: PRODUCTO-CONGELADO OK · gate/run_oracle_tests.sh: ORACULO-TESTS: PASS=16 FAIL=2 UNMET=0 de 18 —
rojos JB11 (el fallo del compensador no se declara), JB12 (fila malformada → null) · gate/suite_full.sh: SUITE-COMPLETA OK
```

```gates
[ ] J5: los 18 checks del oráculo (16 anteriores + JB11, JB12) en verde
  CHECK: gate/run_oracle_tests.sh
  EXPECT: ORACULO-TESTS-VERDE
  EVIDENCE: pending
```

---

## Cierre del lote J (2026-09-04, 16:20)

Recepción de la ronda 3 (`runs3/recepcion.txt`): sello 8/8, write-set limpio, 18/18, suite completa verde
(`Ran 2558`, rojos = los 2 permitidos), diff +270 −26, congelado en `ws-frozen-r3`. Segunda delta de Codex
(`review-delta2/REVIEW-CODEX.md`): «SÍ, ES SEGURO INTEGRAR conforme al tope vinculante de la ronda 3». F-04
cerrada (cinco caminos ejecutados: abort ordinario, abort degradado, abort lanza + reject cierra, ambos
lanzan → `reservation_abort_failed` declarado, reject degradado); F-05 cumple los casos del ledger (lista
mixta, `run_id`/`state` no-str → `run_state_unavailable`); ningún test nuevo verde contra r2; sin
aserciones preexistentes cambiadas.

**F-06 MEDIA → BACKLOG (repro archivado en el dictamen):** `_adopt_on_grant` valida presencia y tipo de
`state`/`run_id` pero no que `owner_session_id` sea `str | None` ni que `state` pertenezca a `RUN_STATES`:
`owner_session_id=7` o `state="ALIEN"` se filtran como «no candidato» y publican `null`. El
`RunManifestStore` real no puede producir esas filas (`RunRecord.validate()`); es la frontera defensiva ante un
colaborador que viola su contrato. Disposición vinculante del tope: se integra y queda en backlog.

Cosmético fuera del producto: `gate/run_oracle_tests.sh:2` decía «lote I» (corregido en la copia archivada,
no en el sello).

Gates de cierre corridos por el orquestador: J1 PRODUCTO-CONGELADO OK · J2/J4/J5 ORACULO-TESTS-VERDE (18/18) ·
J3 SUITE-COMPLETA OK. Integración: `integrar.py` (LF, hash) + suite completa EN el repo: ver `integracion.txt`.
