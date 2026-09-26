# REVIEW-OPUS — lote H (revisor adversarial ciego, familia Anthropic)

Producto verificado por hash antes de leerlo (`sha256sum` desde `lote-H/`):

```
b8271a4d51983e969c02207738f506f6d5a17185b1255add2e3c4e7f259ee549 *ws/tools/dayz_mcp/process_lifecycle.py
4b00de3f4cce4aec93202a25caaacba4093ce096bd820a6be5c8055e48a7d814 *ws/tools/dayz_mcp/dayz_test_tool.py
```

Ambos casan con los del brief. Gate corrido por mí:

- `bash ws/gate/run.sh` → última línea: **`ORACULO-VERDE`** (`ORACULO: PASS=29 FAIL=0 UNMET=0 de 29`)
- `bash ws/gate/suite.sh` → última línea: **`SUITE-ACOTADA OK`** (11 módulos, 397 tests)

Sondas propias en `review1-opus/probes/` (`harness.py` es mío; no importa nada de
`ws/tools/tests/` ni del oráculo). Comandos exactos al final de cada hallazgo.

---

## VEREDICTO

**NO release-safe todavía.** H1 y H4 los doy por cumplidos; D2 aguanta los cuatro
ataques que le he hecho con `runs.json` reales, incluida la lectura por el árbol
anterior; el sobre H3 aguanta forma del `run_id`, doble diagnóstico, recuento de
snapshots y copia-vs-recálculo; el camino feliz y `resolve_stop_run` no tienen
regresión y el diff contra `lote-G/ws-frozen-r8` es puramente aditivo (ninguna
validación desaparecida).

Lo que rompe es **el anillo (H2/H5)**: cinco de los seis caminos de retirada
publican el diagnóstico ANTES de persistir el manifiesto, así que una retirada
que falla se publica como hecha sobre un run que sigue presente y activo. El
único camino con el orden correcto es `stop_run`, que es exactamente el que el
oráculo vigila (`H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED`). Y como el reaper
reintenta cada 30 s, la retirada fallida se repite hasta desalojar el anillo
entero: 32 pasadas (16 min) borran los diagnósticos legítimos y el sobre
`run_not_found` de H3 —el producto de este lote— degrada a `ToolError` pelado.

Dos hallazgos ALTA con repro ejecutable, uno MEDIA, dos BAJA.

---

## HALLAZGOS

### H-1 · ALTA · Cinco de los seis caminos de retirada meten el diagnóstico ANTES de persistir: una retirada fallida se publica como hecha sobre un run que sigue presente

`_retire_run_diagnostic` deja la entrada en el anillo y sólo DESPUÉS se intenta
`manifest.replace`. Si el persist falla, la función devuelve el error —y el
anillo se queda con un diagnóstico que afirma `state: "EXITED"` para un run que
en el manifiesto sigue en `RUNNING_IDLE`/`UNRECONCILED`.

| camino | diagnóstico | persist | qué devuelve si falla |
|---|---|---|---|
| `begin_release_owner` | `process_lifecycle.py:2383` | `:2390` | `cleanup_failed` (except externo) |
| `repair_recovery_fault` | `:2492` | `:2500` | `manifest_drift` (`:2502`) |
| `repair_manifest_recovery` | `:2587` | `:2595` | `manifest_drift` (`:2597`) |
| `_reap_run_locked` | `:2667` | `:2675` | `manifest_failed` (`:2677`) |
| `admin_reconcile` | `:3111` | `:3121` | `manifest_failed` 503 (`:3123`) |
| `stop_run` (**correcto**) | `:2180` | `:2153` (antes) | — |

El orden bueno existe y está a 500 líneas: en `stop_run` el `replace` de
`process_lifecycle.py:2153` va antes del `_retire_run_diagnostic` de `:2180`.

Rompe H2 («runs YA AUSENTES») y la invariante fail-closed de H5 («un stop
fallido no entra al anillo»), que el oráculo sólo comprueba sobre `stop_run`.

**Repro (dos caminos distintos, medidos):**

```
cd lote-H/ws/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  <venv-python> ../../review1-opus/probes/p2_anillo.py
```

Salida literal:

```json
{"probe": "P2A/reap-persist-falla", "pases": [[], [], []], "replace_intentado": 3,
 "run_sigue_presente": true, "estado_en_manifiesto": "RUNNING_IDLE",
 "anillo": ["12345678-…-1234567890ab", "…", "…"],
 "estado_publicado_en_el_anillo": ["EXITED", "EXITED", "EXITED"],
 "event": ["run_reaped", "run_reaped", "run_reaped"]}
{"probe": "P2A/status-publica-las-dos-cosas", "runs": ["12345678-…"],
 "diagnosticos": ["12345678-…","12345678-…","12345678-…"], "mismo_run_en_ambos": true}
{"probe": "P2C/admin_reconcile-falla", "resultado": {"error": "manifest_failed", "_http_status": 503},
 "estado_en_manifiesto": "UNRECONCILED",
 "anillo": [{"run_id": "12345678-…", …, "event": "admin_reconcile",
             "decision": "confirmed", "state": "EXITED"}]}
```

Léase P2A: `reap_dead_runs()` devuelve `[]` **tres veces** —el daemon dice que no
retiró nada— y aun así hay tres diagnósticos `run_reaped / EXITED` de un run que
sigue vivo en el manifiesto. `status()` publica el mismo `run_id` en `runs` y en
`retired_run_diagnostics` a la vez.

El fallo de persist no es hipotético en este árbol: `_persist_locked`
(`:666-690`) escribe a disco con `atomic_write_bytes` y además ejecuta el
`checkpoint`, que puede levantar; en Windows/OneDrive un lock de antivirus o de
sync basta. La sonda lo inyecta sustituyendo `manifest.replace`.

---

### H-2 · ALTA · El reaper reintenta cada 30 s: 32 retiradas fallidas del MISMO run desalojan el anillo y el sobre `run_not_found` de H3 desaparece

Consecuencia medida de H-1, pero con daño propio: `_reap_dead_runs_locked`
(`:2682-2694`) recorre todos los runs reapables en cada pasada y no lleva
memoria de intentos, así que un run cuyo persist falla vuelve a entrar al anillo
en cada pasada. `install_run_reaper` (`daemon.py:776-793`) llama a
`reap_dead_runs()` en bucle con `REAP_INTERVAL_S = 30.0` (`daemon.py:68`).
El anillo es `deque(maxlen=32)` (`process_lifecycle.py:931`): **32 pasadas =
16 minutos** para vaciarlo de historia legítima.

Y eso mata el producto de este lote, porque `execute_dayz_test_stop` exige
exactamente un diagnóstico: `hits = _retired_for(...)`, `if len(hits) != 1: raise`
(`dayz_test_tool.py:842-845`).

**Repro (cadena completa: éxito → poda → inundación → pérdida del sobre):**

```
cd lote-H/ws/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  <venv-python> ../../review1-opus/probes/p4_cadena.py
```

```json
{"probe": "P4/2-tras-la-poda", "run_presente": false, "anillo": ["12345678-…"],
 "sobre": {"kind": "dict", "value": {"status": "failed", "error_code": "run_not_found",
   "daemon_generation_at_launch": "gen-CH", "daemon_generation_current": "gen-CH",
   "generation_changed": false}}}
{"probe": "P4/3-anillo-inundado", "pasadas_que_retiraron_algo": [],
 "B_sigue_presente": true, "B_estado": "RUNNING_IDLE", "copias_de_B": 32,
 "A_sigue_en_el_anillo": false,
 "sobre_de_A": {"kind": "raise", "code": "run_not_found", "campos": []}}
```

El mismo `run_id` A pasa de sobre estructurado con los tres campos a `ToolError`
pelado sin que nadie toque a A. `p2_anillo.py` mide lo mismo en aislado:

```json
{"probe": "P2B/tope-envenenado", "legitimos_antes": 32, "unicos_despues": 1,
 "copias_del_run_vivo": 32, "legitimos_supervivientes": 0}
```

Nota: arreglar H-1 (mover el `_retire_run_diagnostic` detrás del `replace` en los
cinco sitios) elimina también H-2. Lo separo porque el daño —perder el sobre de
runs ajenos— no se ve mirando sólo el sitio del bug.

---

### H-3 · MEDIA · `_copy_generation` fabrica tres `null` cuando el status no trae los campos: fail-open contra D4

`_copy_generation` (`dayz_test_tool.py:798-804`) usa `source.get(...)` para los
tres campos y `_failed_stop_envelope` (`:806-814`) los expande sin comprobar que
existan. Si el `status` no los proyecta, el sobre sale con `null, null, null`
presentados como si fueran medidos. Dos cosas mal:

1. H1 fija `daemon_generation_current` como string siempre (`_generation_projection`,
   `process_lifecycle.py:249`: `current if isinstance(current, str) else ""`).
   `null` ahí es un valor que ningún daemon publica.
2. H3 exige que el camino sin evidencia levante `ToolError("run_not_found")` **sin
   campos inventados**. Tres `null` que nadie midió son campos inventados.

Reachability: el snapshot no es local. `execute_dayz_test_stop` lo pide con
`await runtime.lifecycle_status()` (`dayz_test_tool.py:830`) → `server.py:1210-1211`
→ `control_client.py:228-229` `POST /lifecycle/status` → `loopback.py:3048`
`lifecycle.status(client)`, es decir **otro proceso**. Un daemon de una build
anterior devuelve filas sin proyección y el sobre sale con tres nulls.

**Repro:**

```
cd lote-H/ws/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  <venv-python> ../../review1-opus/probes/p3_sobre.py
```

```json
{"probe": "P3B/run_not_active-sin-proyeccion", "resultado": {"kind": "dict",
 "value": {"status": "failed", "run_id": "12345678-…", "error_code": "run_not_active",
 "daemon_generation_at_launch": null, "daemon_generation_current": null,
 "generation_changed": null}}}
{"probe": "P3B/run_not_found-diag-sin-campos", "resultado": {"kind": "dict",
 "value": {"status": "failed", "error_code": "run_not_found",
 "daemon_generation_at_launch": null, "daemon_generation_current": null,
 "generation_changed": null}}}
```

Arreglo mínimo: exigir las tres claves presentes (y `daemon_generation_current`
string) antes de construir el sobre; si faltan, `raise` como en el resto de los
caminos sin evidencia.

---

### H-4 · BAJA · El `reason` libre del operador viaja al anillo publicado, con paths y timestamps dentro

`admin_reconcile` mete `reason.strip()` del llamante en el campo `reason` del
diagnóstico (`process_lifecycle.py:3111-3116`). Es el único de los seis eventos
cuyo `reason` no es un literal del árbol (`"stopped"`, `"released"`,
`"confirmed_repair"`, `"backup_restored"`, `"all_processes_gone_or_foreign"`).
Ese anillo se publica en `public_status()` (`:2782`), que alimenta el status
provider del daemon (`daemon.py:655`). H2 pide diagnósticos «sin timestamps ni
paths»; la forma los cumple (ocho campos exactos, verificado), pero el contenido
de ese campo no lo puede garantizar el árbol.

**Repro** (`p2_anillo.py`, sonda `P2D`):

```json
{"probe": "P2D/reason-libre-en-el-anillo", "reconciled": true,
 "reason_publicado": ["crash dump en C:\\Users\\guill\\OneDrive\\Documentos\\DayZ Projects\\x.mdmp @ 2026-09-04T05:00:00Z"]}
```

Severidad BAJA: lo teclea el propio operador por la vía TTY, no un tercero. Pero
si «sin paths» es una propiedad del artefacto publicado y no una convención,
hace falta sanear o sustituir por un literal.

---

### H-5 · BAJA · Tras un stop con éxito el mismo run se publica a la vez en `status()["runs"]` y en el anillo

La poda de `EXITED` sólo ocurre al cargar el store (`_prune_exited_on_load`,
`process_lifecycle.py:615-641`, invocado desde `__init__` `:550`). En caliente el
run queda `EXITED` en el manifiesto, y `status()` lista **todos** los runs
(`:2762`) junto al anillo (`:2765`). H2 encabeza el anillo como «runs YA
AUSENTES». `public_status()` no tiene el problema: filtra a `_ACTIVE_STATES`
(`:2777`, proyectado en `:2779`).

**Repro** (`p4_cadena.py`, sonda `P4/1`): `doble_publicacion_status: true`,
`doble_publicacion_public: false`.

Sin daño medido en el sobre: con el run presente gana `run_not_active`
(sonda `P3D/presente-y-en-el-anillo`, que devuelve el dict correcto). Lo dejo
como discrepancia de contrato, no como bug de comportamiento.

---

## LO QUE INTENTÉ ROMPER Y AGUANTÓ

Lo dejo escrito porque cero hallazgos en un ángulo es un resultado, no un hueco.

**D2 — manifiesto hacia delante y hacia atrás** (`probes/p1_d2_manifest.py`,
corrido en los DOS árboles):

- `runs.json` de la versión anterior (sin el campo) leído por el árbol nuevo:
  carga, `field: null`; sobrevive a `_clone`, `get`, `list_runs`, `replace`,
  recarga, `recover_after_restart` (`released`, campo intacto), `release_owner`
  (→ `RUNNING_IDLE`, campo intacto) y `quarantine_legacy_active`
  (→ `UNRECONCILED`, campo intacto).
- `runs.json` escrito por el árbol nuevo con el campo poblado, leído por
  `lote-G/ws-frozen-r8/tools` (sha256 `6eab79a9…`, sin el campo en `RunRecord`):
  **carga sin error**, ignora la clave extra. No hay crash de downgrade.
- `_persist_locked` emite siempre la clave (`dataclasses.asdict`), incluso `null`.

**Anillo:** tope de 32 bajo 8 escritores × 200 entradas con 4 lectores
concurrentes → `len_final: 32`, `max_visto: 32`, cero excepciones (`P2E`).
Orden de locks: 2 000 `status()` + `_publish_retired_diagnostics()` contra 2 000
retiradas → sin deadlock (`P2F`); `_retire_run_diagnostic` y
`_publish_retired_diagnostics` toman sólo `_activity_lock`, y los caminos de
retirada lo toman siempre dentro de `_operation_lock` (nunca al revés).
Ocho campos exactos en `RetiredRunDiagnostic` (`:522-531`), verificado en la
salida de `P2C`.

**Sobre H3:** `run_id` en mayúsculas, sin guiones, `urn:uuid:` y `{…}` → todos
`bad_run_id` levantado, sin sobre (`P3A`). Dos diagnósticos del mismo run
(distintos o idénticos) y cero diagnósticos → `ToolError("run_not_found")`
pelado (`P3C`). Un solo `lifecycle_status` antes del despacho en el camino del
sobre (`P3E`, `lecturas: 1`). Campos copiados y no recalculados: un status
incoherente a propósito (`launch == current` con `generation_changed: true`)
viaja tal cual (`P3F`).

**Regresiones:** `resolve_stop_run` mantiene el gating por estado
(`STARTING`/`STOPPING`/`UNRECONCILED`/`EXITED` → `run_not_active`;
`RUNNING`/`RUNNING_IDLE` pasan; `never_started` pasa) (`P3G`). El diff completo
contra `ws-frozen-r8` de los dos ficheros es **puramente aditivo**: no hay una
sola línea borrada salvo las dos sustituciones de `dataclasses.asdict(run)` por
`self._projected_run(run)` en `status`/`public_status` y la refactorización de la
fila del box. Ninguna validación desaparecida.

**H1 — acreditación del lanzamiento:** el campo se sella en la transición a
`STARTING` con `self._current_generation() or None` (`:1642`), de modo que un
daemon sin generación deja `None` y `generation_changed` sale `null`, nunca
`false` (`_generation_projection`, `:250`). Comprobado además que el camino de
extensión (`existing is not None`) no puede arrastrar una generación vieja: exige
`existing.state == "RUNNING"` (`:1525`) y un run `RUNNING` no sobrevive a un
reinicio (`recover_after_restart` lo pasa a `RUNNING_IDLE`, `:856-859`).

---

## BACKLOG

- `server.py`, oráculo, ledger, actividad y bindings: fuera de producto por el
  brief. La traducción `DayzTestToolError → ToolError` de `server.py:3054-3055`
  la doy por buena porque el gate H4 la mide y la corrí yo.
- `_reap_dead_runs_locked` no lleva memoria de intentos fallidos por run. Aunque
  se arregle H-1, un reaper que no converge merece backoff o un contador.
- Un `RunRecord` con `daemon_generation_at_launch == ""` pasa `validate()`
  (`:515-518`) y `_generation_projection` lo trata como `None` (`:247-248`).
  Es tolerancia coherente, no defecto; lo anoto por si la spec quiere prohibirlo
  en la validación.

## NO VERIFICADO

- **No he corrido un daemon real con versión desfasada.** La reachability de H-3
  la sostengo por la cadena de llamadas citada (`dayz_test_tool.py:830` →
  `server.py:1210` → `control_client.py:228` → `loopback.py:3048`), no por un
  experimento con dos builds vivas. El fail-open en sí sí está medido (`P3B`).
- **No he provocado el fallo de persist con un disco real**, sino sustituyendo
  `manifest.replace`. Que `_persist_locked` pueda fallar en Windows lo sostengo
  por lectura del código (`:666-685`: `atomic_write_bytes` + `checkpoint` que
  puede levantar), no por un fallo de I/O reproducido.
- **`begin_release_owner` (`:2383/:2390`), `repair_recovery_fault` (`:2492/:2500`)
  y `repair_manifest_recovery` (`:2587/:2595`)**: los cito por lectura del orden
  de las dos llamadas, no con repro propio. Los dos que sí ejecuté
  (`_reap_run_locked` y `admin_reconcile`) tienen exactamente la misma forma.
- No he tocado in-game nada: todo es en proceso, con dobles.
- No he leído `review*/`, `runs*/` ni `ws/STATE.md`.

## HIPÓTESIS (sin repro que corra — no cuentan como hallazgo)

- Si `daemon_generation` fuese `""` (sólo alcanzable construyendo
  `ProcessLifecycle` a mano; el daemon siempre inyecta un `uuid4().hex`,
  `daemon.py:815` y `:332`), un run sellado publicaría `generation_changed: true`
  contra una generación actual desconocida — un fail-open contra D4. No lo
  presento como hallazgo porque no encuentro un camino de producción que lo
  alcance.
- Dos retiradas legítimas del mismo `run_id` (stop con éxito → restauración de un
  backup donde el run vuelve activo → segunda retirada) dejarían el anillo
  ambiguo para ese run y el sobre degradaría a `ToolError`. La duplicación la
  tengo medida sólo por la vía del persist fallido (H-2); esta variante no la he
  montado.
