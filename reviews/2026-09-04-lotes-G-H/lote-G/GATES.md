# Gates: lote G — ficha 16 (`4d66`), bloques PASS 1 y 2: actividad

Scope: la ocupación del box publica por run **`activity_state`** (`recent` / `stale` /
`unknown`, umbral 900 s) y **`last_activity_age_s`**, y `occupancy_error_fields` los proyecta
al error público `active_run_exists` junto al `age_s` que ya lleva. La actividad se alimenta
de un evento nuevo `run_command_activity` en el audit jsonl, escrito en el **enqueue
aceptado de las dos ramas** (normal y `_enqueue_exec_enforce`), con **memoria sticky de
fallo de escritor por `(daemon_generation, run_id)`** que fuerza `unknown`/null.

**Fuera de alcance a propósito**: los bloques PASS 3 y 4 de la misma ficha —el sobre
estructurado de `dayz_test_stop`, los `retired_run_diagnostic` y la desambiguación de
`lifecycle_stop_outcome`— van en su propio lote. La memoria sticky se indexa por
`(daemon_generation, run_id)`, así que el plumbing de generación nace aquí y aquel lo consume.

Este ledger lo escribió y lo corrió la sesión orquestadora ANTES de delegar. **No lo edites.**

Estado medido antes de empezar, en este workspace y con este comando:

```
PASS=4  FAIL=7  UNMET=0  de 11
```

**Los 4 PASS son la línea que no se puede romper**, y son todos de preservación:
  - las seis claves previas del error público, **con su valor** (no basta con que estén);
  - las seis claves previas de `box.runs`;
  - **un PID owned antiguo sigue ocupando la caja** — si el cálculo de actividad se cuela en
    la decisión de ocupación, otro arranque pisaría una sesión viva. Es el daño más caro
    posible de este lote.

## Calibración: qué está probado y qué NO

Mutantes corridos por la orquestadora: **3 de 3 cazados**, control positivo (BASE) rojo.
Los tres atacan la línea de preservación, que es la mitad que hoy puede romperse.

**Lo que NO está calibrado, y se dice en vez de disimularlo.** Los tres mutantes que la
propia ficha nombra —`success clears sticky`, `los campos sólo en box.runs` y
`fallback al start`— atacan código que todavía no existe. Calibrarlos hoy exigiría escribir
la implementación dentro del mutante, o sea calibrar una conjetura del orquestador en vez de
la entrega. **Los corre la sesión orquestadora contra el código entregado, antes de aceptar
el lote.** Los siete rojos actuales son triviales —el sujeto está ausente— y un mutante
contra ellos no probaría nada.

Tres defectos del propio ledger murieron en esta calibración, y explican por qué existe:
`box_occupancy(now=...)` toma un **epoch absoluto** y no un desplazamiento, así que el
umbral 899/901 medía una constante; `start_run` fallaba con `identity_unavailable` sin
snapshot del guard y dejaba el box vacío; y los controles comprobaban presencia de clave en
vez de valor.

```gates
[ ] G1: activity_state, last_activity_age_s y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

## Notas que evitan gates imposibles, todas medidas

- **La venv NO tiene pytest.** La suite es `unittest`.
- `box_occupancy(now=...)` acepta reloj inyectado y **saltea la caché** cuando se le pasa
  (`process_lifecycle.py:2425`), así que el umbral es conducible con precisión. Pero `now`
  es un **epoch absoluto**: `_run_age_s` hace `now - min(stamps)` sobre `creation_time_utc`
  (`:171-179`). Los tests entregados nunca usan esa costura.
- El fallo de escritor se inyecta devolviendo `False` desde el sumidero de auditoría, que es
  el mecanismo del propio árbol.
- La suite completa (2321 tests) no es gate de este workspace. La corre la orquestadora al
  integrar, contra `Ran 2321 tests, FAILED (failures=2, skipped=6)`, cuyos dos rojos
  conocidos son `test_full_source_hash_is_frozen` y
  `test_removing_only_marker_lines_restores_frozen_source_hash`.
- **Este lote es data-crítico** (`D3=true`): toca el audit jsonl y el estado del lifecycle.
  Además del gate, pasa por una auditoría `rigorous-data-audit` con auditores por ángulo
  antes de declararse release-safe. Está decidido por el humano y no es negociable desde aquí.

---

## Ronda 2 — el ledger estaba mal, y la entrega tenía razón

Esto lo escribe la sesión orquestadora el 2026-09-03, después de recibir la ronda 1 en verde
(11/11) y **antes** de volver a delegar. El criterio era mío y estaba mal; lo que sigue es la
corrección, no un reproche a la entrega.

### Lo que falló, medido

**El oráculo de la ronda 1 no abría el audit ni una vez.** Su sumidero es un objeto de lista,
así que `getattr(sink, "__self__")` es `None`, `_audit_jsonl_path()` devuelve `None` y
`_latest_jsonl_activity_epoch` contesta `"absent"` **siempre**. Los once checks medían la
ruta en MEMORIA. P3 —la durabilidad por el jsonl, que es la ficha entera— quedó sin medir.

Consecuencia directa: la entrega pudo ir 11/11 **mientras escribía en el audit desde una
lectura**, porque el gate no podía ver el fichero.

Y el G4 sellado inyectaba el fallo de escritor alrededor de `box_occupancy`, no de un
enqueue. Para que ese fallo fuera observable, la entrega tuvo que hacer que la LECTURA
tocara al escritor. **Eso lo forzó mi gate.** La entrega lo dijo en `DISPUTAS`, que es
exactamente el protocolo, y tenía razón.

### Los tres hallazgos, con la sonda que los mide

Sonda con forma de producción (`JsonlAuditWriter` real, no un sumidero de lista):
`probe_readpath_write.py`.

| # | Hallazgo | Evidencia |
|---|---|---|
| 1 | Una lectura pura de `box_occupancy` **escribe** en el audit | 1 evento: `{"event":"run_command_activity","reason":"activity_observed","activity_epoch":1784073641.0}`. La ficha manda lookup **read-only/acotado** |
| 2 | El evento fabricado es `run_command_activity`, **el mismo que el lookup consulta**, para un run con CERO comandos, y retrofechado al `creation_time_utc` del proceso | El audit pasa a afirmar actividad de comando que no ocurrió. Lo lee M17 tras un reinicio, la ficha 17, o un humano |
| 3 | La basal sale de `record.creation_time_utc`, no del `lifecycle_start_outcome` acreditado que la ficha nombra | La sonda confirma que ese evento **sí está** en el audit, con `run_id`, `decision="started"`, `state`, `daemon_generation` y `timestamp_utc`, y que nadie lo lee |

Y uno adyacente, de endurecimiento y no agujero vivo: `if generation and event_generation
not in (None, generation)` acepta como ACTUAL un evento **sin** `daemon_generation`. Hoy no
es explotable —`JsonlAuditWriter` siempre lo estampa y `_require_generation` rechaza el
vacío—, pero es fail-open en una frontera de generación, y la ficha manda `unknown` ante
evidencia ambigua, nunca inferencia.

### Lo que NO estaba mal, y no se toca

`G1` es correcto y cita la ficha literal: *«start de la generación actual sin comandos a
899/901 s produce recent/stale»*. Llegué a sospechar que mi propio G1 consagraba el mutante
«fallback al start»; **es falso**, y lo dice la propia ficha: ese FAIL es más estrecho
—*«permitir que start basal … venza al **sticky**»*— y el mutante F3 sí lo cazó.

### Estado medido con el oráculo corregido, antes de delegar

```
PASS=13  FAIL=3  UNMET=0  de 16
```

Los tres rojos son R1-READONLY, R2-BASAL y R4-AMBIGUA, uno por hallazgo, cada uno con su
mensaje de causa. **Los 13 verdes son la línea que no se puede romper**, e incluyen dos
nuevos: R3-HEREDADO (un run de otra generación es `unknown`) y R5-DURABLE (un comando
aceptado deja **un** evento y la edad se mide de él — su `len(evs) == 1` también vigila que
no vuelva la escritura desde la lectura).

### Alcanzabilidad: probada, y el prototipo NO viaja

Un gate que no puede ponerse verde es un deseo con nombre de gate. Se construyó un prototipo
mínimo —lookup read-only, basal desde `lifecycle_start_outcome`, generación ausente ⇒
ambigua— y dio **16/16 verde**; después se revirtió y el árbol volvió al hash exacto de la
entrega (`6ff3e28d…`). `alcanzabilidad.py` conserva el experimento. El prototipo **no** se le
entrega a nadie: calibrar la entrega contra mi conjetura sería medir mi conjetura.

### Qué se rompe, medido y no adivinado

Con el prototipo aplicado cae **exactamente un** test entregado, de 200 en cuatro módulos:

```
tests.test_box_occupancy.RunCommandActivityTest
  test_writer_fault_is_sticky_unknown_for_the_generation   FAIL
```

Es el que conduce el fallo de escritor por `box_occupancy` — escrito contra mi oráculo viejo.
Se reescribe conduciéndolo por el enqueue aceptado, que es la ruta del contrato. No se
arregla restaurando la escritura desde la lectura.

```gates
[ ] G1: activity_state, last_activity_age_s y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 3 — el sustrato cambia por decisión humana

Escrito por la sesión orquestadora el 2026-09-04, tras recibir la ronda 2 en verde (16/16) y
someterla a **dos revisiones independientes**: Codex (otra familia, sólo producto) y una
auditoría `rigorous-data-audit` de siete ángulos con verificación adversarial. La ronda 2 era
correcta contra su gate. El gate no era el problema esta vez; el **sustrato** sí.

### La decisión, y de quién es

La actividad **deja de leerse** del `events.jsonl`. Pasa a vivir en estado propio del daemon,
indexado por `(daemon_generation, run_id)`. **El audit sigue registrando el evento; deja de
ser la fuente de verdad.** Write-only, no read-write.

Lo decidió el humano tras ver la medición de abajo. **Se desvía a propósito de la línea 34 de
la ficha 16** («M17 busca de forma read-only/acotada … en `events.jsonl` + backups»), y queda
escrito aquí para que nadie lo lea como un descuido.

Lo que hace sólido el rediseño, y no un atajo: actividad y sticky se indexan por generación,
y **lo único que destruye la memoria del daemon es un reinicio — que cambia la generación y
ya obliga a `unknown` por contrato**. La memoria es exactamente tan durable como hace falta.
Consecuencia lateral: la basal deja de necesitar acreditación por fichero. El daemon arrancó
ese run, en esta generación, y lo recuerda; eso es una credencial más fuerte que parsear un
fichero que cualquiera puede escribir.

### Lo que forzó la decisión, medido

**En Windows, LEER el audit hace FALLAR su escritura.** El escritor publica con `os.replace`;
un lector con el fichero abierto lo bloquea. Y un fallo de escritura marca el par
`(generación, run)` como sticky-`unknown`: **el mecanismo se envenena solo cuanto más se le
consulta**. La ronda 2 introdujo el primer lector en runtime del audit, así que el defecto
nace con ella.

```
control directo: os.replace con un handle de lectura abierto -> PermissionError [WinError 5]
caso real, 3 lectores sobre un audit de 854.400 B:
    escrituras intentadas : 776
    escrituras FALLIDAS   : 776  (100,0 %)
```

Honestidad sobre ese 100 %: es un arnés de **saturación** (19.201 lecturas en 6 s). El
auditor midió 4 de 118 a cadencia realista. Los dos números son ciertos a su carga; lo que
importa es que el modo de fallo existe y crece con el uso.

El rediseño **disuelve cinco de los once hallazgos por construcción**, porque los cinco son
defectos del LECTOR: la carrera de Windows, la rotación sin backups, la procedencia
falsificable, el `-Infinity` que publica `age=inf`, y el coste `O(runs × líneas)`.

### Lo que NO disuelve, y es el trabajo de esta ronda

| # | Hallazgo | Quién lo encontró |
|---|---|---|
| **N1** | `record_box_command_activity`: con `owner_session` no vacía y el run **sin dueño**, `owned` sale vacía, el `elif` es inalcanzable y **no se registra nada**. Un `RUNNING_IDLE` comandado se publica `stale` con la edad del arranque, y `occupancy_error_fields` le cuelga el hint «páralo» | R9: 3 ángulos + pasada cruzada |
| **N2** | El reverso exacto: el cleanup por **expiración de lease** encola `vehicle_release` con `internal=True`, cae por el `elif` y **sí** marca actividad. El instante en que el sistema detecta el abandono es el instante en que sella el run como recién usado | R9 (ángulo security) |
| **N3** | `_box_cache` no se invalida al mutar actividad: tras un fallo de escritor, la lectura cacheada sigue diciendo `stale`/`recent` mientras la real es `unknown`. Y `loopback.py` llama al lector **sin `now`**, que es justo la rama cacheada | Codex H-02 |
| **N4** | El sustrato: corromper o borrar `events.jsonl` no puede cambiar la respuesta | la decisión |
| **N5** | Hoy la cláusula «heredado ⇒ `unknown` hasta actividad durable nueva» es **inalcanzable**: un run heredado queda sin dueño, y por N1 no vuelve a registrar actividad jamás | R9 (state-machine) + cruzado |

N1 y N2 son **la misma raíz**: la selección de objetivos está invertida respecto a la
intención en las dos direcciones.

### Estado medido antes de delegar

```
PASS=13  FAIL=5  UNMET=0  de 18
```

**Los 13 verdes son la línea que no se puede romper.** Incluyen los tres controles de
preservación de siempre y dos que vigilan de cerca este rediseño: `R1-READONLY` (una lectura
no escribe) y `R5-RASTRO` (**un comando aceptado sigue dejando UN evento en el audit** — deja
de ser autoridad, no deja de registrar).

### Alcanzabilidad: probada en su núcleo, y el alcance se declara

Prototipé el núcleo —sustrato en memoria y selección de objetivos— y dio **16/18 sin romper
ningún verde**; los dos rojos restantes son exactamente los que decidí no prototipar (N2 y
N3, arreglos locales de satisfacibilidad obvia). Después se revirtió al hash de la entrega
(`e575586e…`). El prototipo **no** viaja al implementador.

Ese prototipo **cazó una imposibilidad mía antes de costar una ronda**: N4 exigía `recent`,
pero la basal en memoria es el arranque del proceso mientras el reloj del check salía del
evento de start — dos anclas distintas, siete semanas de diferencia en la fixture. Corregido
a «el estado **no cambia**», y con la guarda de que `sano` no sea ya `unknown`, o el check se
satisfaría haciendo desaparecer el sujeto.

### Verificación cruzada de las dos revisiones

De los 5 hallazgos de Codex reproduje **5 de 5** yo mismo (H-04 sólo en su forma acotada: el
`-Infinity` únicamente hace daño cuando queda como único candidato). De la auditoría R9,
7 confirmados de 15 afirmaciones.

⚠ **La «tasa de refutación 0,53» que publicó mi workflow no es una tasa de refutación.** Sólo
3 de 15 se refutaron con veredicto; **5 se quedaron sin veredicto ninguno** porque mi
consolidación casaba veredicto con afirmación por igualdad de título y el verificador
reformula los títulos. Entre esas 5 silenciadas estaba la rotación, que Codex encontró por su
cuenta y que reproduje con una rotación real. Es un fallo de mi instrumento, no de la
auditoría, y las 5 se adjudicaron a mano.

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 4 — identidad del destino y coherencia de la caché

Escrito el 2026-09-04 tras recibir la ronda 3 en verde (18/18) y su segunda revisión Codex.
El sustrato nuevo aguantó: el audit dejó de ser autoridad, la actividad no decide `occupied`,
stop ni reap, y los cinco defectos del lector siguen disueltos. Lo que falló es más fino.

### El hueco del gate, que es lo primero que hay que decir

Los checks de la ronda 3 llamaban a `record_box_command_activity` **directamente**. Verificaban
el registrador, no el **cableado** `enqueue → binding → registrador`. Por ahí pasó H-01.

Los tests entregados tenían el mismo hueco por otra puerta, y esto lo encontró el revisor:
`fence_helpers.bind_both_peers` ata `run_id="test-run"` por defecto, y
`RunCommandActivityTest._bind_state` lo llama sin el `run_id` real. O sea que el test verde
`test_accepted_enqueue_records_activity_on_both_branches` **comandaba un binding de otro run**
y luego aceptaba la atribución por cardinalidad. Verde por el motivo equivocado.

Los checks de esta ronda conducen el enqueue de verdad, con dos runs activos y el binding
apuntando al que NO tiene dueño.

### Los cuatro hallazgos, reproducidos por mí, 4 de 4

| # | Hallazgo | Medido |
|---|---|---|
| **H-01** | El enqueue conoce el `run_id` exacto del binding (`loopback.py:954-965`), lo descarta, y el destino se reconstruye por dueño o cardinalidad | comando sellado para `run-2`, frescura acreditada a `run-1`; con varios activos sin dueño, **cero** eventos |
| **H-02** | `_capture_start_activity` toma `min()` de **todos** los procesos: extender un run heredado importa a la generación nueva un sello anterior a este daemon | `sello_gen_B == epoch de un proceso de 2026-06-01` |
| **H-03** | Sembrar la basal en `start_run` no invalida la caché | tras un `start_run` OK, la lectura cacheada sigue diciendo `occupied=False, runs=[]` |
| **H-04** | Carrera de publicación: un lector que calculó antes de una invalidación publica su snapshot **después** | verdad `unknown`, la caché publica `stale` |

H-01 subsume el hueco (a) que yo había dejado abierto: con varios runs activos y el comandado
sin dueño, mi parche de la ronda 3 (`if not targets and len(active) == 1`) no aplicaba. El
arreglo correcto no es ampliar el respaldo: es **no tirar la identidad exacta que ya se tiene**.

### Estado medido antes de delegar

```
PASS=16  FAIL=5  UNMET=0  de 21
```

Los cinco rojos son N1-ATRIBUCION, N5-REINICIO (depende de N1), N6-GENERACION, N7-CACHE-START
y N8-CACHE-CARRERA. **Los 16 verdes son la línea que no se puede romper**, e incluyen
`N4-SUSTRATO` (corromper o borrar `events.jsonl` no mueve la respuesta), `R1-READONLY`,
`R5-RASTRO` y `N2-INTERNO`, ya arreglado en la ronda 3.

### Alcanzabilidad: probada donde importa, y el alcance se declara

Prototipé **N6, N7 y N8** porque son los que tocan verdes existentes —N6 cambia la basal de la
que dependen G1 y G3, N7/N8 cambian la caché de la que depende N3—: **19/21 sin romper ningún
verde**, y los dos rojos restantes son exactamente los que no prototipé. **N1 y N5 no los
prototipé**: su arreglo es fontanería (llevar `binding.run_id` del enqueue al registrador) y no
toca ningún verde. Revertido al hash de la entrega (`4c9e60f6…`); el prototipo no viaja.

### Convergencia, no noria

Cuenta y severidad bajan ronda a ronda: 11 hallazgos con 3 P1 en la ronda 2, **4 hallazgos sin
ningún P1** en la ronda 3, concentrados en dos familias (identidad del destino y coherencia de
la caché) y con el arreglo nombrado por el revisor. El sustrato no se vuelve a tocar.

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 5 — se deja de cachear la actividad, y el contador se traza a todos sus call-sites

Escrito el 2026-09-04 tras la ronda 4 (21/21 verde) y **dos revisiones que convergieron**:
Codex (5 hallazgos) y una re-auditoría R9 acotada (6 verificadas, **0 refutadas**). Las dos
apuntaron al mismo componente.

### La decisión, y de quién es

**La actividad deja de cachearse.** Se cachea lo caro —sondas de proceso y decodificación de
argv— y los dos campos de actividad se recalculan en cada lectura desde la memoria viva, que
es una consulta a un diccionario. Lo decidió el humano tras ver el patrón de abajo.

### El patrón que forzó la decisión

`_box_cache` produjo hallazgos en **tres revisiones consecutivas**, cada uno con su arreglo
correcto y cada uno seguido de un agujero nuevo en el mismo sitio:

| ronda | hallazgo | arreglo |
|---|---|---|
| r2 | no se invalida al mutar actividad | correcto |
| r3 | sembrar la basal no invalida · carrera de publicación | correctos |
| r4 | quien pierde la carrera **devuelve** lo falso · un stop no invalida | — |

Eso no es mala suerte: es la señal de que el problema no está en el parche sino en la
pregunta. Recalcular la actividad en cada lectura disuelve la familia y retira el aparato.

### Ampliación de alcance, declarada

La R9 midió que **el contador de revisión no se incrementa en la mayoría de las mutaciones**:
ni el alta del run provisional en `STARTING` (P1, con la caja publicándose LIBRE mientras DayZ
arranca y un bloqueo del dueño legítimo de hasta `BOX_CLAIM_TTL_S` = 600 s), ni `stop_run`
(cinco `manifest.replace` sin tocar la caché), ni `adopt_run`, `admin_reconcile`,
`begin_release_owner` o `repair_recovery_fault`. El **reaper sí invalida y tiene test propio**:
es la mitad que falta del par.

Esto va más allá de lo aprobado y se dice. El argumento para incluirlo: **este lote introdujo
el contador como mecanismo de coherencia**, y un mecanismo que no se incrementa donde debe
está incompleto por construcción. Trazar la invariante a todos sus call-sites (DZ-R7) es
trabajo de quien la introdujo.

### Estado medido antes de delegar

```
PASS=21  FAIL=4  UNMET=0  de 25
```

Rojos: `N9-FRESCA` (quien pierde la carrera devuelve la actividad vieja), `N11-SIN-BINDING`
(sin binding se acredita por cardinalidad, o se deja envejecer), `N12-MONOTONA` (una escritura
tardía hace retroceder la frescura) y `N13-ESTRUCTURAL` (la caja cacheada publica un run ya
parado, **sin concurrencia**, con dos llamadas seguidas).

### Dos decisiones de gate que conviene leer

**N10 retirado, con su razón.** El hallazgo H1 del revisor —un `start_run` rechazado deja
frescura en el run heredado que restaura— es real y está **reproducido por él, no por mí**: mi
fixture no alcanza `_capture_start_activity` porque la ruta de verdad es adoptar-y-extender, y
montar eso costaba más de lo que el check acreditaba. Dejar un UNMET perpetuo habría
convertido el gate en un imposible. Viaja al brief con su cita, y el implementador escribe el
test. **Ese arreglo no lo cierra el gate: lo cierro yo al recibir.**

**Dos checks míos fallaban por el andamio, no por el producto.** N10 pasaba en verde sin haber
ejercitado nada (el `start_run` moría en la validación) y N13 daba rojo con `stop_ok=False`.
Los dos llevan ahora guarda anti-vacuidad: si el fixture no deja el estado necesario, el
veredicto es `UNMET`, no `PASS` ni `FAIL`. Un verde vacío miente y un rojo por el motivo
equivocado manda a arreglar lo que no está roto.

### La cuenta honesta de las revisiones

De los 5 hallazgos de Codex en la ronda 4 reproduje 5. De la R9, 6 verificadas y 0 refutadas
—y esta vez el workflow casa veredictos **por índice, no por título**, que es lo que la vez
anterior mandó 5 afirmaciones al cubo de «sin verificar», una de ellas real.

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 6 — la caja se deriva de UNA foto, y la familia entera se retira

Escrito el 2026-09-04 tras la ronda 5 (25/25 verde) y la cuarta revisión de Codex: **6
hallazgos, los seis con intercalación ejecutable, y la respuesta a la pregunta de proceso**:
«los seis tienen intercalación ejecutable y pertenecen a familias ya visitadas, no a una
familia nueva». La cuenta por ronda —11, 4, 7, 6— no converge.

### La decisión, y de quién es

**Snapshot coherente.** Lo decidió el humano el 2026-09-04: `box_occupancy` toma bajo lock una
foto de manifiesto + actividad + revisión, suelta, hace las sondas caras fuera y deriva la
respuesta de la foto. Ataca la causa estructural: hoy `box_occupancy` deriva una vista sobre
**dos almacenes mutables independientes** (manifiesto y mapas de actividad) con E/S cara en
medio y sin foto coherente, y parchear entrelazados de uno en uno no termina.

### Tres decisiones del ledger que conviene leer

**1. El P1 de la ronda 5 era insatisfacible, y es mío.** «Ninguna respuesta puede llevar una
actividad que ya no es cierta, ni siquiera la de quien perdió una carrera» no lo cumple
ningún diseño concurrente: entre el instante en que el lector fija su valor y el instante en
que el cliente lo lee siempre cabe una escritura. La misma pregunta cayó en la ronda 3 (N3),
en la 4 (N8), en la 5 (N9) y en la revisión de la 5 (H1): **cuatro ataques distintos sobre
una sola pregunta** (LL-421: el problema es la pregunta, no la métrica). P1 se reformula como
lo que sí es observable: **la caja publicada no mezcla instantes** — es un valor que fue
cierto A LA VEZ, en un instante dentro de la llamada. N9 se retira con su razón y N14 lo
sustituye; lo que N9 protegía de verdad (que la caché no republique actividad superada) lo
siguen midiendo N3 y N8, que son secuenciales.

**2. Qué lock, y cuál no.** La foto NO se toma bajo `_operation_lock`: `stop_run` lo sostiene
mientras termina procesos (segundos), y poner la lectura caliente del loopback detrás de él
serializa cada `box_occupancy` contra cada start/stop/reap. En su lugar: `manifest.list_runs()`
**ya es una foto coherente** (clona bajo el `_lock` del store, `process_lifecycle.py` en
`RunManifestStore.list_runs`), y se toma pegada a UNA foto de `_last_activity` /
`_activity_unknown` / revisión bajo `_activity_lock`, sin E/S entre las dos lecturas.
**Residuo declarado**: la atomicidad estricta entre los dos almacenes no se garantiza; la
ventana es de nanosegundos, no contiene E/S y no hay seam desde el que un check pueda
observarla. Se dice, no se esconde.

**3. El contador de revisión deja de ser el mecanismo de coherencia de `runs`.** Si las filas
no se cachean nunca, olvidar un incremento deja de tener consecuencia sobre ellas: H5 (la
reparación del manifiesto), H6 (la cuarentena legacy) y la familia entera de «¿y en este
call-site también sube?» —hallazgos en r4, r5 y su revisión— se disuelven por construcción.
El contador queda como guarda del caché de **sondas** (`foreign`, `ports_in_use`,
`scan_known`), que es una muestra del sistema operativo con TTL y se declara como tal.
**Residuo declarado**: `occupied = bool(runs or foreign)` puede combinar un `runs` fresco con
un `foreign` de hasta 1,5 s; está acotado y es fail-closed aguas abajo (`start_run` rescanea
el diag por su cuenta; un snapshot desconocido cuenta como ocupado).

### H3: compensación, no retrasar el BOUND (mandato)

El revisor sugería no publicar el binding como `BOUND` hasta que el `RUNNING` sea durable. Eso
reordena la publicación de la que depende cada enqueue —cirugía en la ruta caliente— para una
ventana que mi fixture no alcanza (precedente N10). Mandato: **la actividad acreditada durante
un intento de arranque que no llega a durable no sobrevive al rollback**. `_settle_failed_launch`
olvida los sellos del run restaurado cuyo epoch sea ≥ el inicio del intento, y el run publica
`unknown`. Es fail-closed, y la cola del intento fallido ya la tira `_retire_minted`. Sin check
en el oráculo, por el mismo motivo que N10: test del implementador con rojo-antes, y **lo cierro
yo al recibir**.

### H4: el binding muere con su run (trazado DZ-R7)

Invariante: **ningún binding despachable sobrevive a la salida durable de su run de `RUNNING`.**
La retirada va ANTES de la transición durable en `stop_run` (ya en `STOPPING`, no sólo en
`EXITED`), y en el mismo orden en `begin_release_owner`, `repair_recovery_fault`,
`repair_manifest_recovery`, `_reap_run_locked` y `admin_reconcile`. Un run sin binding es la
degradación segura. N17 lo mide en `stop_run`; el resto lo prueban los tests del implementador
y lo compruebo al recibir.

### Estado medido antes de delegar

```
PASS=25  FAIL=4  UNMET=0  de 29
```

Rojos: `N14-MEZCLA` (una fila `RUNNING` con `activity=unknown`: el estado del instante en que
empezó la lectura y la actividad del instante en que terminó), `N15-RETROCESO` (una
observación sin binding de hace un minuto borra un sello acreditado posterior),
`N17-BINDING-MUERE-CON-SU-RUN` (un enqueue tras el `EXITED` durable devuelve 200) y
`N18-SIN-CACHE-DE-MANIFIESTO` (una mutación durable sin incremento publica `RUNNING` con el
manifiesto en `UNRECONCILED`). `N16-SIN-BINDING-MANDA` es **verde antes por diseño**: guarda de
regresión para que el arreglo de N15 no desactive P3.

### Dos fallos de mi instrumento en esta ronda, para que consten

- **El oráculo imprimió `ORACULO-VERDE` con 3 FAIL.** Inserté el bloque nuevo con `rfind` del
  separador y cayó DESPUÉS del bucle que calcula el veredicto: los checks nuevos entraban en el
  recuento (`de 29`) pero no en `worst`. Lo delató la aritmética, no el color. Ancla corregida
  al separador pegado a `worst = 0`.
- **N14 salió verde sobre el código actual.** La lectura de siembra consumió la barrera de
  `argv_of` y la guarda anti-vacuidad dio por bueno el bloqueo del hilo equivocado. La barrera
  ahora se arma justo antes de lanzar el lector y la guarda exige que quien se bloqueó sea el
  lector y siga dentro cuando llega la mutación. Entonces, rojo.

Las dos son LL-378 en mis propias manos: un check verde antes de delegar no mide nada, y la
línea de veredicto tiene que calcularse DESPUÉS del último check — el `de N` se contrasta con
los checks que uno sabe nombrar.

### La cuenta honesta

De los 6 hallazgos de Codex en la ronda 5, 6 aceptados. Disueltos por construcción: H1 (N14),
H5 y H6 (N18). Arreglados como defectos: H2 (N15 + N16), H4 (N17 + trazado). H3 va al brief
con la cita del revisor y lo cierra el test del implementador más mi verificación al recibir.

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 7 — sin dueño no se despacha, los borrados dejan tumba, el sello va antes que el dato

Escrito el 2026-09-04 tras la ronda 6 (29/29 verde, recibida con sello 4/4 y rojo-antes de sus
12 tests verificado por el orquestador) y **dos revisiones ciegas entre sí**: Codex (2
hallazgos: ALTA + MEDIA) y Opus (3 ALTA). Los cinco están **verificados por el orquestador**:
Codex-H1 reproducido con sonda propia (`verificar_codex_r6.py`); los tres de Opus ejecutando
sus sondas (`review6-opus/probe_h1_revision_skew.py`, `probe_h2_p3_sin_binding.py`,
`probe_h3_p5_rollback.py`: `VEREDICTO ROTO` ×3, rc=1); Codex-H2 aceptado por su repro
determinista y cubierto por la misma decisión que Opus-H3.

### Lo que dijeron sobre las familias, y lo que decido

Codex: «no apareció una familia nueva» (H1 identidad/autoridad del destino; H2 frontera de
linearización de la actividad). Opus: «las dos son NUEVAS» (F-A: el sello de invalidación se
muestrea en otro instante que el dato que sella; F-B: el borrado no deja tumba y el escritor
muestrea su instante antes de la E/S). Las dos lecturas son ciertas a distinta altura: son
mecanismos nuevos de la misma raíz que la ronda 6 atacó —**instantes de muestreo**— y por eso
esta ronda los cierra por construcción (una tumba por época; un sello que es cota inferior),
no por call-site. El gate cubría ambas familias sólo en forma secuencial (N13/N15/N16/N18);
N21 y N22 añaden la forma concurrente.

### Tres decisiones del ledger

**1. Sin dueño no se despacha, pero el binding sobrevive (P6).** Codex-H1: tras un release
reconocido el run pasa a `RUNNING_IDLE` sin dueño y su binding sigue BOUND y despachable
(`world_spawn` con 200 sobre un proceso que nadie posee). La corrección sugerida —retirar el
binding en el release— rompería el reattach de la ficha 17: el servidor vivo tendría que
relanzarse para obtener un binding nuevo. Decisión: **la propiedad es «despachable», no
«existe»**. Sin dueño el enqueue se rechaza con un código propio y distinto de
`binding_retired`, la cola pendiente del antiguo dueño se vacía, el bridge sigue haciendo poll
sin recibir comandos, y `adopt_run` rehabilita el mismo binding. N19 lo mide en
`release_owner`; N20 protege la rehabilitación (verde por diseño).

**2. Los borrados dejan tumba (P7).** Opus-H2/H3 y Codex-H2 son el mismo defecto por tres
puertas: `_write_command_activity` muestrea `epoch` ANTES de la E/S del audit (6-16 ms
medidos) y sella DESPUÉS; cualquier borrado que caiga en medio —una observación sin binding
posterior, o la compensación de un rollback— queda anulado por una escritura con época más
vieja que él. Decisión: **todo borrado registra su época** (`(generación, run_id) → tumba`) y
**ninguna escritura con época ≤ tumba aterriza**, sea crédito de comando o basal. La
compensación de P5 se ejecuta **después** del rollback durable, con la tumba en ese instante,
y olvida sólo los sellos con época dentro de `[inicio del intento, rollback]`: un crédito
posterior al rollback pertenece al run restaurado y se conserva (la objeción de Codex a «un
segundo pop»). El sticky `unknown` sigue mandando sobre todo.

**3. El sello del caché de sondas es cota INFERIOR del dato (P8).** Opus-H1, y es de
autoridad: `_take_box_snapshot` lee el manifiesto en t0 y la revisión en t1 > t0; una
mutación entre ambos deja sondas filtradas contra el manifiesto viejo (un PID reciclado se
descarta por «registrado») guardadas bajo la revisión nueva, y un lector limpio compone filas
nuevas con sondas viejas: **caja vacía con un DayZDiag ajeno vivo**. Decisión: la revisión se
muestrea ANTES que cualquier dato que certifica; la guarda de escritura del caché compara
contra ese sello. N22 lo mide por su observable: tras una mutación en vuelo, el siguiente
lector vuelve a sondear.

### Estado medido antes de delegar

```
PASS=30  FAIL=3  UNMET=0  de 33
```

Rojos: `N19-SIN-DUENO-NO-DESPACHA` (release reconocido, enqueue con 200),
`N21-TUMBA` (el escritor con época anterior al borrado resucita el sello → `recent`),
`N22-SELLO-ANTES-DEL-DATO` (sondas 2 → 2: el lector siguiente reutiliza sondas de antes de la
mutación). `N20-ADOPCION-REHABILITA` verde por diseño. Los 29 anteriores siguen verdes.

### Un fallo de andamio en la calibración, para que conste

N19 salió `UNMET` y N20 rojo en la primera calibración por un verbo equivocado: `camera_get`
(de cliente) con `peer="server"` → `bad_peer` 400. Corregido a `world_spawn`, la guarda
anti-vacuidad hizo su trabajo: dio UNMET en vez de un rojo falso, y el rojo de N20 llevaba el
payload que lo delataba. Recalibrado: N19 rojo por la razón correcta, N20 verde.

### Lo que el gate no mide y cierro yo al recibir

La rama reconocida de `begin_release_owner` (N19 sólo ejercita `release_owner` directo),
`admin_reconcile` con supervivientes (publica `RUNNING_IDLE` sin cerco), el vaciado de la cola
pendiente en el release, y la ventana exacta de Codex-H2 (crédito tras la compensación y
antes del rollback durable). Tests del implementador con rojo-antes, verificados por mí.

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 8 — cierre de la 7: la suite en verde bajo el contrato P6, y los gates demostrados

Escrito el 2026-09-04 tras recibir la ronda 7 (sello 4/4; oráculo **33/33 VERDE corrido por
el orquestador**; suite acotada **ROJA en un test**; +393/−43 líneas normalizadas). Sin
producto nuevo.

### Qué pasó en la ronda 7, y de quién es cada cosa

- **La lane Grok CLI murió con 402** («Grok Build usage balance exhausted») a los 120 s, sin
  tocar el árbol. Relevo: **Cursor Grok 4.6** (`cursor-grok-4.6-high` por `cursor-agent`),
  identidad verificada en el evento `init` del stream-json.
- **El worker de Cursor no pudo ejecutar ni un shell.** `cursor-agent` 2026.09.02 importa los
  hooks de Claude Code desde `~/.claude/settings.json` (ruta fija `claudeUserConfigPath` en
  su bundle; `CLAUDE_CONFIG_DIR` no cuenta) y ejecuta el envoltorio PowerShell por bash:
  `Hook blocked: eval: syntax error near '&'` en cada llamada, subagentes incluidos. Entregó
  a ciegas, con tests escritos sin correr; el oráculo salió 33/33 igualmente. Remedio, medido
  con control positivo (3 bloqueos → 0): HOME de sandbox sin `.claude/` y con `.cursor`
  enlazado por junction al real; va en `runner_cursor.sh`.
- **P6 se refina a MUTACIONES**, decisión del implementador que el orquestador acepta y
  escribe: sin dueño se rechazan las mutaciones no internas (`run_not_owned`, 409); las
  lecturas siguen despachando. N1-ATRIBUCION exige que `camera_get` llegue a un run sin
  dueño, y la autoridad está en las mutaciones, no en las lecturas de diagnóstico.
- **El único rojo de la suite es consecuencia correcta de P6**: la segunda mitad de
  `test_accepted_enqueue_attributes_activity_to_binding_run_among_several` encola
  `exec_enforce` (mutación) sobre `run-2` sin dueño y esperaba 200. El test se adapta
  conservando su propósito (dar dueño a `run-2`), no se relaja el cerco.

### Lo que esta ronda tiene que demostrar

Los dos gates literales corridos por el worker, el rojo-antes REAL de los tests de la
ronda 7 contra `ws-frozen-r6` (la ronda 7 no pudo correrlos), y que
`test_failed_extend_discards_activity_credited_in_the_confirm_window` sigue verde con la
compensación movida tras el rollback. El orquestador repite los dos gates al recibir.

### Estado medido antes de delegar

```
run.sh:   ORACULO: PASS=33 FAIL=0 UNMET=0 de 33 · ORACULO-VERDE
suite.sh: test_box_occupancy FAILED (failures=1) · resto OK · SUITE-ACOTADA ROJA
```

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 9 — sin dueño no se despacha NADA, el cerco va antes de publicar, y la tumba es la frontera del rollback

Escrito el 2026-09-04 tras la revisión de Codex del producto final (rondas 7-8): **3 hallazgos con
repro determinista, ninguno de familia nueva** (H1/H2 identidad-autoridad del destino; H3
sello-vs-dato). Aceptados los tres.

### Una corrección al ledger de la ronda 8, y es mía

En la ronda 8 acepté el refinamiento «P6 cerca mutaciones; las lecturas son diagnóstico y
siguen». Codex lo refuta con repro: una lectura por el binding de un run `RUNNING_IDLE` entra
con 200 **y acredita actividad** (`_note_run_command_activity`) — una petición de nadie
refresca la frescura de un run que nadie posee; y una mutación `internal=True` también pasa.
La autoridad durable del run no permite despacho: **ni lectura, ni mutación, ni interno**.
Mi N1 y N5 exigían lo contrario por FIXTURE, no por contrato: `dos_runs` tenía a `run-2` sin
dueño porque entonces la pregunta era «¿se acredita al binding o al dueño que llama?». Esa
pregunta se conserva con `run-2` **con OTRO dueño** (B) que `run-1` (A); y N5 adopta el run
heredado antes de comandarlo, que es el flujo real tras un reinicio. Un refinamiento aceptado
por comodidad del gate, no por el contrato: LL-376 en la otra dirección (lo que el redactor
afirmó de más).

### Las tres decisiones

**P6 estricto y linealizado.** Con el run en `RUNNING_IDLE` se rechaza TODO comando dirigido a
él (`run_not_owned`), sin excepción por `mutation` ni `internal`. El cleanup interno del
release (p. ej. `vehicle_release`) se entrega o se descarta ANTES de que `RUNNING_IDLE` sea
durable. Y el cerco es un estado lógico de `ServerState` bajo su propio lock —el mismo que usan
enqueue y poll—, activado y con la cola vaciada ANTES de la publicación durable
(`manifest.release_owner`), revertido si la persistencia falla, y levantado por `adopt_run`.
Es P4 («retirar antes de la transición durable») aplicado al release. Codex-H2 medido: el poll
ganaba al vaciado y entregaba un `world_spawn` legítimo a un dueño que ya no existía.

**P7' la tumba es la frontera del rollback.** Codex-H3: `rolled_back_at` se muestreaba
DESPUÉS de `manifest.replace(target)` (rollback visible) y de invalidar el caché; un crédito
legítimo en ese intervalo veía `RUNNING`, acreditaba, y la compensación lo borraba (época ≤
tumba). Decisión: **cerco transitorio de compensación** — antes del `replace` del rollback
se marca el run como «compensando» bajo `_activity_lock`; los créditos que lleguen mientras
dura se DESCARTAN en el único punto de escritura (`_seal_activity_locked`); tras persistir se
ejecuta la compensación (ventana `[inicio del intento, ahora]`), se eleva la tumba y se
levanta el cerco. Ningún crédito queda mal fechado: los de antes del cerco son del intento
(se olvidan), los de dentro se descartan (fail-closed: `unknown` hasta el siguiente), los de
después aterrizan.

**El gate cambia con el contrato, y se dice.** `dos_runs` con `run-2` (dueño B, `RUNNING`),
N5 con adopción, N23 (una lectura sobre `RUNNING_IDLE` se rechaza y no acredita), N25 (un
poll durante el release, con la barrera justo después de persistir, no se lleva la cola).
Codex-H3 no tiene check (exige el fixture adoptar-y-extender, precedente N10): test del
implementador con rojo-antes verificado por mí.

### Estado medido antes de delegar

```
PASS=33  FAIL=2  UNMET=0  de 35
```

Rojos: `N23-LECTURA-SIN-DUENO` (200 + un evento de actividad sobre un run sin dueño) y
`N25-RELEASE-CERCA-ANTES-DE-PUBLICAR` (el poll se lleva `world_spawn`). N1/N2/N11 verdes con
el fixture nuevo; N5 verde con adopción (un `UNMET` intermedio por `active_run_exists` con
dos runs: N5 pasó a `build()`, porque no necesita dos).

### Sobre el lote H, que corre en paralelo sobre este producto

Si esta ronda toca `process_lifecycle.py` (lo hará: cerco de compensación) el lote H se
rebasa con un encargo corto al worker sobre el árbol final de G, no a mano.

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

---

## Ronda 10 — el cerco se ata a la transición, y el poll revalida

Escrito el 2026-09-04 tras la ronda 9 (35/35 + suite 349 OK corridos por el orquestador; las
cinco sondas de las revisiones anteriores dejaron de reproducir) y la revisión ciega de Codex
sobre ella: **4 hallazgos con repro** (ALTA, MEDIA, MEDIA, BAJA), aceptados los cuatro.

### Lo que Codex encontró, y lo que dice de la ronda 9

- **ALTA — `repair_manifest_recovery` produce `RUNNING_IDLE` sin cerco.** Llama a
  `RunManifestStore.recover_after_restart()` en caliente sin `_fence_runs`, y `record_poll`,
  que suelta el lock entre copiar la cola y entregar (`expire_due`, sondas), al retomarlo sólo
  revalida identidad y prefijo de la cola: un `world_spawn` copiado con dueño se entrega sin
  dueño. Es **exactamente el hueco que la 9 decía haber cerrado** («la invariante se ató a los
  call-sites, no a la transición»): el método único cubre release, no repair; `admin_reconcile`
  escribe `RUNNING_IDLE` a mano (cerca localmente); `release_all_running_owners` es otro
  productor público sin caller. La ronda 9 fue ROJA por esto en su propia pregunta y yo no lo
  vi al recibir porque las sondas de Opus (transición-luego-poll) no alcanzan esta ventana.
- **MEDIA — la compensación no oculta el sello que ya existía.** `_compensating` impide
  escrituras nuevas, pero `_activity_for_run_id` no lo consulta: un lector entre el `replace`
  durable del rollback y `_forget_attempt_activity` publica `recent` del intento fallido.
- **MEDIA — familia NUEVA: ciclo de vida asimétrico del cerco.** `retire_run` no borra el
  `run_id` de `_fenced_runs`; reap, reconcile sin supervivientes y repairs terminales acumulan
  una marca por run hasta reiniciar.
- **BAJA — `exec_enforce` que pierde la carrera con un release devuelve `enqueue_cancelled`**
  en vez de `run_not_owned` (fail-closed, pero colapsa el motivo que P6 exige).

### Las decisiones

**P6'' — el cerco se ata a la TRANSICIÓN.** Toda persistencia que pueda dejar un run en
`RUNNING_IDLE` (release, `recover_after_restart` en repair, la escritura de reconcile, y
`release_all_running_owners` aunque no tenga caller) pasa por un único `fence+drain →
persistir → confirmar/unfence`. Y **el poll revalida cerco y estado durable al retomar el
lock**, antes de comprometer la entrega: es la defensa que sobrevive a cualquier productor
olvidado.
**P7'' — mientras compensa, la lectura publica `unknown`.** La tumba es la frontera también
para el lector.
**P6''' — retirar borra el cerco**, dentro del mismo `ServerState._lock` de `retire_run`.
**P6'''' — `exec_enforce` conserva el `fence_error_code`.**

### Estado medido antes de delegar

```
PASS=35  FAIL=3  UNMET=0  de 38
```

Rojos: `N26-POLL-REVALIDA` (poll con la cola copiada, `repair_manifest_recovery` en su ventana
sin lock, y entrega `world_spawn`), `N28-CERCO-NO-SE-ACUMULA` (tras release + reap, `run-1`
sigue en `_fenced_runs`; caja blanca declarada) y `N29-EXEC-ENFORCE-RUN-NOT-OWNED`
(`enqueue_cancelled`). **N25 recableado**: ahora pasa `lifecycle.bindings = state`, con lo que
mide el quiesce del lifecycle (verde en la 9, **rojo en la r8**: medido) — el worker de la 9 lo
había señalado en su NO PUDE VERIFICAR y tenía razón.

### Dos fallos de instrumento en esta ronda, para que consten

- **N26 v1 salía verde sobre la 9**: bloqueaba dentro de `recover_after_restart` y el poll
  llegaba DESPUÉS de la transición, donde la defensa del loopback por estado durable ya lo para.
  No medía el hallazgo. La v2 bloquea en la ventana sin lock del poll (seam:
  `loopback.command_requires_lease`, función de módulo que el poll llama por cada comando
  copiado) y reproduce.
- **Un `write_text` con `newline` ilegal truncó `oracle.py` a 0 bytes**: `open('w')` trunca
  antes de que se valide el argumento. La copia sellada, verificada idéntica a la fuente antes
  del fallo, la restauró. Un sello no es sólo integridad: es backup.

### Sobre el R9 de cierre

La primera corrida del workflow terminó con las **siete verificaciones caídas** por el filtro
de Fable 5.1 (`reasoning_extraction`) sobre prompts con afirmaciones + código; los ángulos y
la pasada cruzada sí terminaron. `confirmados=0` con `SIN_VERIFICAR=todo` no es un veredicto.
Se reanudó desde caché con la etapa de verificación en Opus. Lo que confirme entra en esta
ronda si llega antes del lanzamiento; si no, en la siguiente.

```gates
[ ] G1: la actividad publicada y su proyeccion al error publico
  CHECK: gate/run.sh
  EXPECT: ORACULO-VERDE
  EVIDENCE: pending

[ ] G2: los ficheros de test que cubren lifecycle, ocupacion y loopback siguen verdes
  CHECK: gate/suite.sh
  EXPECT: SUITE-ACOTADA OK
  EVIDENCE: pending
```

> **Nota de ejecución (2026-09-04):** la ronda 10 NO corre en `lote-G/ws` (el R9 lo está leyendo por líneas citadas y el lote H ya lo había fusionado con la r9): corre como `lote-H/runs3` sobre el árbol fusionado, con este mismo brief adaptado en rutas y con los dos oráculos como gate. El sello `runs10/GATE-SEAL.txt` deja constancia del gate de G en su forma final (38 checks).

---

## Ronda 11 — cierre (ejecutada como `lote-H/runs4` sobre el árbol fusionado)

Escrito el 2026-09-04. La ronda 10 (transición cercada, poll que revalida, lector que respeta la
compensación, retire que borra el cerco, `exec_enforce` con su código) entró sobre el árbol
fusionado con G 38/38, H 40/40 y suite OK corridos por el orquestador. El R9 de cierre, con la
verificación en Opus, confirmó 14 afirmaciones de 18 (`runs10/R9-VERIFICADO.md`); esta ronda
cerró las que son defecto (P9-P18, ledger en `lote-H/gates_ronda4.md`): crédito en vuelo que
no aterriza tras el cerco, retire que cierra lo que descarta, estado ilegible ⇒ rechazo, valla
de resultado que sobrevive a la retirada, compensación aunque el rollback falle, recovery
reparable, reconcile acreditable, hints sin efectos, basal de extensión = max, residuos
limpiados en EXITED.

Gate final de G: **42 checks** (`oracle.py` SHA-256 `4c48642a…`), sellado en `runs11/`.
Recepción de la ronda de cierre: sello 4/4, +581/−57, **G 42/42, H 40/40, suite de 11 módulos
OK** corridos por el orquestador; 13 tests nuevos rojos sobre la r3 verificados por el
orquestador; sin disputas.

Producto final (fusionado): `process_lifecycle.py` `70179879…`, `loopback.py` `d607e62e…`,
`dayz_test_tool.py` `bca97e26…`. Pendiente: revisión final ciega (Codex + Opus), suite completa
en el repo (baseline HEAD `00d4303`: 2.357 tests, 2 fallos por nombre —
`test_full_source_hash_is_frozen`, `test_removing_only_marker_lines_restores_frozen_source_hash`
— y 6 skipped), integración por rutas exactas con LF, commit.
