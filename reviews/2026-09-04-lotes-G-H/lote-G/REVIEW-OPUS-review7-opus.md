# REVIEW-OPUS — ronda 7, revisor adversarial ciego (familia Anthropic)

Producto verificado por hash antes y después de trabajar (no escribí en `ws/`):

```
6eab79a98c55101a8942195a7f96a50f7ddeb0223a920f70f77735ff92143a86 *ws/tools/dayz_mcp/process_lifecycle.py
91cb695ccb4681949dd83db263856cea33890624f914134ce8ac035ac8e82bcb *ws/tools/dayz_mcp/loopback.py
```

Ambos casan con los del brief. Sondas y este documento son lo único que escribí, todo bajo
`...\lote-G\review7-opus\`.

## Gate

Corrido dos veces, con el producto BIT A BIT IDÉNTICO en las dos.

- Al empezar (≈05:24), `bash ws/gate/run.sh` → última línea:
  `ORACULO: PASS=33 FAIL=0 UNMET=0 de 33` (seguida de `ORACULO-VERDE`).
- Al terminar (≈05:44), `bash ws/gate/run.sh` → última línea:
  `ORACULO: PASS=33 FAIL=2 UNMET=0 de 35`  (RC=1)
- `bash ws/gate/suite.sh` → última línea, en las dos pasadas: `SUITE-ACOTADA OK`

La diferencia no es del producto: `ws/gate/oracle.py` se reescribió a las 05:38:20 y `GATES.md` a
las 05:41:29 (mtime), mientras el producto está congelado en 05:05/05:06. El oráculo actual es
`sha256 2a5ccbbb70335d1d7b3ca53ae35460579efa63d433246606c5d1b6e5e52f9e98`. Los dos checks nuevos que
caen son `N23-LECTURA-SIN-DUENO` y `N25-RELEASE-CERCA-ANTES-DE-PUBLICAR`. Lo digo como dato de
proceso, no como hallazgo mío: el gate cambió bajo mis pies y **el resultado que un tercero lea
depende de a qué hora corrió el gate**. Ver §BACKLOG y §NO VERIFICADO.

## VEREDICTO

**NO RELEASE-SAFE para P6.** Dos hallazgos, ambos con repro que corre, ambos del mismo defecto:
el cerco de P6 se aplica en el *ingress* de comandos (que está bien cerrado: probé las tres vías y
las tres rechazan) pero el vaciado de la cola pendiente está atado a **tres call-sites concretos**
(`process_lifecycle.py:2189`, `:2212`, `:3003`) en vez de a la **transición** a `RUNNING_IDLE`. Hay
dos caminos más que producen esa transición y no drenan: la rama asíncrona de `begin_release_owner`
y `repair_manifest_recovery`. En los dos, el bridge recibe y ejecuta una mutación del mundo
(`world_spawn`) contra un run que ya no tiene dueño. Ninguno de los dos lo ve el gate de 33 checks
que estaba verde cuando empecé.

P7 (tumba) y P8 (sello del caché) **aguantan** las intercalaciones que les tiré, con control positivo
en cada sonda para que el verde no sea vacuo. Las regresiones P1 (lectura sin `_operation_lock`,
filas sin caché) también aguantan. Los residuos declarados los di por declarados y no los conté.

## HALLAZGOS

### H1 — ALTA — `begin_release_owner`: la rama de limpieza asíncrona deja la cola del antiguo dueño

**Código.** `process_lifecycle.py:2308`

```python
                    extra = self.manifest.release_owner(session_id, lease_id)
                    if extra:
                        self._invalidate_box_cache()
                    released.extend(extra)
```

`RunManifestStore.release_owner` (`process_lifecycle.py:765-782`) es exactamente lo que pasa
`RUNNING → RUNNING_IDLE`:

```python
                    run.state = "RUNNING_IDLE"
```

Las otras dos llamadas a ese mismo método sí drenan: `release_owner` (`:2186` → `:2189
self._drain_pending_for_runs(changed)`) y la rama **síncrona** de `begin_release_owner` (`:2209` →
`:2212`). La tercera, la del hilo `cleanup()`, no. Y el camino de entrega (`ServerState.record_poll`,
`loopback.py:2025-2060`) **no vuelve a mirar el estado durable del run**: el único filtro allí es el
binding, y el binding sigue `BOUND` por diseño de P6.

**Cómo se llega.** El hilo `cleanup()` sólo arranca si la sesión tiene algún run *no reconocido*
(`launch_operation_id` puesto y `launch_acknowledged` falso, `:2199-2206`). Basta con que la misma
sesión posea además un run reconocido: ése no entra en `unacknowledged`, lo recoge el
`release_owner` de la línea 2308 y se queda con su cola intacta.

**Repro.** `sonda_H1_drain_async_release.py` (RC=0 = roto). Desde `ws\tools`, con
`PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1`:

```
enqueue antes del release        : status=200 body={'id': 1, 'peer': 'server', 'cmd': 'world_spawn'}
cola del binding antes           : ['world_spawn']
estado durable de run-ack        : RUNNING_IDLE owner=None
cola del binding despues         : ['world_spawn']
enqueue NUEVO tras el release    : status=409 error=run_not_owned
poll del bridge tras el release  : bind=BOUND commands=['world_spawn']
resultado del cleanup           : {'terminal_safe': True, 'runs_released': ['run-ack', 'run-unk']}
VEREDICTO H1: ROTO (la cola sobrevive y se despacha)
CONTROL rama sincrona            : estado=RUNNING_IDLE cola=[]
CONTROL: OK (la rama sincrona SI drena)
```

El control positivo (mismo escenario sin el run no reconocido → rama síncrona) sale con la cola
vacía. Sin él, el hallazgo sería "el drenaje no existe"; con él queda acotado a la rama asíncrona.

**Fallo concreto.** La sesión A encola `world_spawn`, suelta el lease y se va. El daemon publica el
run como `RUNNING_IDLE` (sin dueño) y a la vez le entrega al bridge el spawn de A. Si otra sesión B
adopta el run en ese hueco — `adopt_run` rehabilita el mismo binding y **tampoco drena**: las únicas
llamadas a `_drain_pending_for_runs` son `:2189`, `:2212` y `:3003`, y `adopt_run` (`:2122-2181`) no
está entre ellas — B recibe un objeto en su mundo que ella no pidió. El rechazo `run_not_owned` que
sí funciona (409 arriba) sólo cubre lo que se encola *después*.

**Fix del que respondo por forma, no por implementación.** Atar el drenaje a la transición, no al
call-site: que `_drain_pending_for_runs(...)` cuelgue de todo retorno no vacío de
`manifest.release_owner`, incluido el de la línea 2308.

### H2 — MEDIA-ALTA — `repair_manifest_recovery`: `recover_after_restart()` en caliente, sin cercar ni drenar

**Código.** `process_lifecycle.py:2500-2503`

```python
            try:
                restored.recover_after_restart()
            except Exception:
                return self._manifest_repair_failure("manifest_drift")
```

`recover_after_restart` (`:813-840`) pasa a `RUNNING_IDLE` todo run `RUNNING` con dueño. Ese bucle no
va precedido ni seguido de `_retire_run_bindings` ni de `_drain_pending_for_runs` para los runs que
libera (el `_retire_run_bindings` de `:2494` sólo cubre los no reconocidos que el repair mata), ni de
`_invalidate_box_cache()`.

**Por qué cuenta.** No es una ruta de arranque: `repair_manifest_recovery` se sirve desde un endpoint
HTTP del daemon vivo (`loopback.py:3101` → `_repair_lifecycle_recovery_fault` → `loopback.py:3313`).
El daemon lleva bindings vivos con sus colas cuando entra ahí.

**Repro.** `sonda_H2_repair_recovery_sin_cerco.py` (RC=0 = roto):

```
enqueue con dueno vivo           : status=200 body={'id': 1, 'peer': 'server', 'cmd': 'world_spawn'}
repair_manifest_recovery         : {'terminal_safe': True}
estado durable de run-x          : RUNNING_IDLE owner=None
binding tras el repair           : BOUND
cola del binding tras el repair  : ['world_spawn']
enqueue NUEVO tras el repair     : status=409 error=run_not_owned
poll del bridge tras el repair   : bind=BOUND commands=['world_spawn']
VEREDICTO H2: ROTO (RUNNING_IDLE sin drenar ni retirar)
```

Le doy MEDIA-ALTA y no ALTA porque exige una falta armada de recovery y una llamada de admin; el
daño, cuando ocurre, es idéntico al de H1.

## Lo que NO rompí (negativos con control, no ausencia de intento)

Los apunto porque un negativo sin control no vale: cada sonda lleva un caso que *debe* pasar, para
que el verde no sea "todo sale unknown" o "el caché nunca acierta".

- **P7 — la tumba aguanta en crédito y en basal.** `sonda_H4_p7_tumba.py` (RC=0).
  `T1` mete la observación sin binding (época T0+10) **dentro del audit** del escritor acreditado
  (época T0+1) — el audit corre fuera de `_activity_lock` (`process_lifecycle.py:1055-1063`), que es
  el hueco real: el sello no aterriza (`unknown`). `T2a` prueba la **basal**:
  `_capture_start_activity` con `creation_time_utc` por debajo de la tumba no sella; el
  discriminador es que, si aterrizara, la lectura daría `stale` (edad 7250 s) y no `unknown`.
  Controles `T2b` y `T3`: basal y crédito por encima de la tumba sí sellan (`recent`).
  Estructuralmente: `_seal_activity_locked` (`:1045`) es el **único** escritor de `_last_activity`
  (verificado por grep de todas las apariciones del dict en los dos ficheros) y consulta la tumba en
  la línea 1047; los dos borrados (`:1034` y `:1106`) levantan tumba (`:1035`, `:1107`).
- **P8 — el sello es cota inferior del dato.** `sonda_H3_p8_sello_vs_dato.py` (RC=0).
  `C0` demuestra primero que el caché SÍ acierta sin mutación (si no, todo lo demás sería vacuo).
  `I1` mete la mutación entre el muestreo de la revisión (`:2718`) y `list_runs()` (`:2720`);
  `I2` la mete **dentro** de `_collect_probes` (vía `argv_of`), con un fallo de caché forzado antes y
  un `assert` de que la intercalación llegó a ejecutarse. En los dos casos la lectura en carrera no
  queda cacheada y el lector siguiente vuelve a sondear (contador de `diag_probe` 1→2→3).
- **P6 en el ingress — las tres vías de mutación rechazan.** `sonda_H5_vias_de_despacho.py` (RC=0).
  Rama normal → `409 run_not_owned`; `exec_enforce` (doble cerco, `loopback.py:1712` y `:1741`) →
  `409 run_not_owned`; sin binding, la mutación **no puede** caer en la cola legacy
  (`loopback.py:1195-1201`) → `409 legacy_unbound` y `_legacy_queues['server']` vacía. `camera_get`
  despacha 200, que es lo que dice la nota de contrato de mi brief. Control con el run en `RUNNING`:
  200.
- **P1 — la lectura no toma `_operation_lock` y las filas no se cachean.**
  `sonda_H6_p1_lectura_sin_lock.py` (RC=0). Con `_operation_lock` retenido por otro hilo,
  `box_occupancy()` vuelve; el control (`release_owner`, que sí lo toma) se queda bloqueado. Y con el
  caché de sondas caliente, una transición durable colada por debajo del lifecycle aparece ya en la
  fila publicada (`RUNNING → RUNNING_IDLE`).
- **P4/P5 — retirada antes de la transición durable.** Verificado leyendo los seis caminos:
  `:1910`→`:1912`, `:1978`→`:1981`, `:2257`→`:2259`, `:2285`→`:2287`, `:2304`→`:2305`,
  `:2406`→`:2408`, `:2494`→`:2496`, `:2567`→`:2569`, `:3001`→`:3005`. En todos el
  `_retire_run_bindings` precede al `manifest.replace`. La compensación de P5
  (`_persist_failed_launch_target`, `:1320-1337`) corre después del `replace` durable, como dice P7.

## FAMILIAS

**Familia visitada, mecanismo nuevo.** H1 y H2 caen bajo *identidad/autoridad del destino* — la
autoridad del run cambia y algo que ya estaba en vuelo no se entera. Pero el mecanismo no es ninguno
de los cinco listados: no es coherencia de caché (las colas no son un caché), ni frontera de
generación, ni sello-vs-dato, ni borrado-sin-tumba. Es:

> **La invariante se ató a los call-sites de la transición en vez de a la transición.**

`_drain_pending_for_runs` existe, funciona y está bien puesta en tres sitios; el defecto es que
`manifest.release_owner` y `recover_after_restart` se pueden llamar desde un cuarto y un quinto sitio
sin ella. Es exactamente el patrón que R7 manda cazar (grep de la API cambiada y de la operación
opuesta, y por cada hit preguntar si asume la invariante vieja): los tres call-sites con drenaje
salen del grep de `release_owner`, el cuarto (`:2308`) está en el mismo fichero y a 100 líneas del
tercero, y el quinto entra por otra API (`recover_after_restart`) que hace la misma transición.

El síntoma diagnóstico de esta familia: **el gate verde cubría el cerco (N19, N20) pero no la
enumeración**. Un check por comportamiento no encuentra un camino que nadie enumeró. Lo que lo
encuentra es el censo de asignaciones `run.state = "RUNNING_IDLE"` y de llamadas a quien las hace —
que son cinco, no tres.

## BACKLOG

1. **El gate se movió durante la revisión.** `oracle.py` a las 05:38:20 y `GATES.md` a las 05:41:29,
   con el producto congelado en 05:05/05:06. Con el mismo binario, la respuesta a "¿está verde?"
   pasó de `ORACULO-VERDE` (33/33) a `PASS=33 FAIL=2 de 35`. Si el ledger registra "verde" sin
   registrar el sha del oráculo, la afirmación no es reproducible. Sugerencia: sellar oráculo y
   producto juntos en cada veredicto.
2. **`N23-LECTURA-SIN-DUENO` contradice la nota de contrato de mi brief.** Mi brief dice, literal,
   que las lecturas como `camera_get` siguen despachando sobre un run sin dueño porque son
   diagnóstico; el check nuevo exige rechazarlas. Eso es un cambio de contrato entre ronda 7 y
   ronda 8, no un defecto del producto que me dieron: mi `V4` afirma el contrato del brief. Hay que
   decidir cuál manda antes de contar ese FAIL como deuda.
3. **`repair_manifest_recovery` no invalida el caché de la caja tras `recover_after_restart()`**
   (`:2500-2503`; el `_invalidate_box_cache()` de `:2499` queda dentro del bucle anterior). Impacto
   acotado a `foreign`/`ports_in_use`/`scan_known` durante ≤1,5 s, porque las filas nunca se cachean
   — es decir, cae dentro del residuo ya declarado. Lo dejo aquí, no como hallazgo.
4. **`RunManifestStore.release_all_running_owners` (`:790-812`) no tiene ningún llamador** en el
   paquete (grep sobre `ws/tools/dayz_mcp/*.py` sin `tests`). Es un tercer productor de
   `RUNNING_IDLE` esperando a que alguien lo use sin drenar. Borrarlo o darle el drenaje.
5. **`_durable_run_state` falla abierto en excepción** (`loopback.py:1121-1122`: `except Exception:
   return None`, y `_enqueue_run_rejection` trata `None` como "despacha"). Hoy `manifest.get` es un
   lookup en dict más un clon y no lanza, así que no tengo repro; pero la dirección del `except` es
   la contraria a la del resto del fichero.

## NO VERIFICADO

- **La amplificación de H1/H2 vía `adopt_run` no la ejecuté.** Lo que sí está verificado es lo
  estático: `adopt_run` (`:2122-2181`) no llama a `_drain_pending_for_runs` — las tres únicas
  llamadas son `:2189`, `:2212` y `:3003`. Que la sesión B reciba el comando de A tras adoptar es
  inferencia sobre esos dos hechos, no una traza que haya corrido.
- **Nada de esto se probó contra el bridge real ni contra un daemon HTTP vivo.** Las seis sondas
  usan `FakeGuard`/`AuditSink` y llaman a `ServerState.enqueue_command` y `record_poll` en proceso.
  Lo que demuestran es que la cola sobrevive y que `record_poll` la entrega; no que DayZ ejecute el
  spawn.
- **Los dos FAIL del oráculo nuevo no los diagnostiqué.** `N25` es de mi misma familia (un poll que
  se lleva la cola durante el release) pero es otra intercalación —la rama síncrona contra el lock
  del loopback—, no la mía. No leí su implementación: leerla me habría desviado del producto, y el
  brief me pedía ciego.
- **No leí `review*/`, `runs*/`, `ws/STATE.md` ni `ws/BRIEF.txt`.** Sólo los dos ficheros del
  producto, sus dependencias directas (`instance_fence`, `session_coordination`, `runtime_state`),
  los helpers de `tests/` que necesité para montar los bancos, y el gate a través de `run.sh` /
  `suite.sh`.
- **Cobertura declarada.** Ataqué P6 (ingress + colas + los cinco productores de `RUNNING_IDLE`), P7
  (crédito, basal, tumba, sticky) y P8 (dos intercalaciones), más P1 y P4/P5 por inspección con una
  sonda. No ataqué: la frontera de generación, la atribución por binding, la frescura monótona ni el
  quarantine de identidad legacy — esos los di por cubiertos por el gate anterior y no los sondeé.
