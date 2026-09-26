# Revisión ciega — agujero 1 del coordinador (privilegio de tombstones)

**Revisor:** sesión ciega, sin contacto con el implementador.
**Base medida:** copia aislada `C:\Users\guill\AppData\Local\Temp\mcp-laneA1-20260819`.
**Método:** diff por hash contra `baseline.csv`, 12 mutaciones sobre producción,
2 corridas de la suite entera bajo mutación, réplica independiente del rojo
inicial, y sondas de comportamiento contra la API pública del coordinador. Cero
verificación de runtime (no hay juego).

**Estado del árbol al terminar:** restaurado byte a byte al estado de la entrega
(mismos 4 ficheros cambiados vs `baseline.csv`, 0 faltantes; sha256 de los tres
de producción idéntico al de partida). No se escribió nada en OneDrive.

---

## 1. Veredicto

**CONFIRMADO** — la invariante se cumple en el código entregado, y los tres modos
de fallo del spec §6 están cerrados.

**Con dos hallazgos MAYORES de cobertura**: dos de las líneas de producción que se
escribieron para comprar esta invariante **no las sujeta ningún test de los 1626**.
La invariante es correcta *hoy*; no está *protegida* contra una regresión mañana.

### La medida que sostiene el CONFIRMADO

Matriz completa dueño/extraño × `enqueue`/`acquire`, con 128 tombstones acuñados
por un extraño vía `cancel_operation`, contra el código parcheado sin mutar
(`scratchpad\probe_final.py`):

| Quién | `enqueue` | `acquire` |
|---|---|---|
| Holder del lease activo | 409 `operation_conflict` | 409 `operation_conflict` |
| Dueño del claim de caja | 202 `queued` | 200 `active` |
| Extraño **en `_box_queue`** sin claim | **503 saturado** | **503 saturado** |
| Extraño unseen | **503 saturado** | **503 saturado** |

Ningún ocupante recibe el 503; los dos no-ocupantes sí. El 409 del holder es el
comportamiento que el spec §2 describe como correcto ("ni siquiera llega al 409
`operation_conflict`: recibe 503" era el bug; ahora llega).

---

## 2. Tabla de mutaciones

Las 4 que pedía el brief más 8 propias. Cada una se aplicó sobre producción, se
midió, y se restauró verificando sha256 (estado final idéntico al de partida:
`session_coordination.py` `f4be851cc0de554b`, `control_client.py` `1237a9f41a3d2e02`,
`server.py` `8fecf62ea647317c`).

Nombres cortos: **T1** `..._box_claimer_enqueue_...`, **T2** `..._box_claimer_acquire_...`,
**T3** `..._saturated_error_tells_stranger_...`, **T4** `..._toolerror_includes_tombstone_recipe`,
**C1/C2/C3** los tres de caracterización.

| # | Qué rompí | Línea | Cayó | Se quedó verde | Veredicto |
|---|---|---|---|---|---|
| **M1** | Quitar `_box_claiming` del helper | `session_coordination.py:2755-2756` | T1, T2 | T3, T4, C1, C2, C3 | ✅ como se pedía |
| **M2** | Quitar el privilegio de `enqueue`, dejarlo en `acquire` | `:720` | **solo T1** | **T2** + resto | ✅ exacto: T1 y T2 miden caminos distintos |
| **M3** | Quitar el `hint` del payload | `:2777-2780` | T3 | T4, resto | ✅ (T4 fabrica su propio 503; ver §4c) |
| **M4** | Invertir el privilegio (al extraño, no al dueño) | `:2752-2757` | T1, T2, T3, **C1, C2** | T4, C3 | ⚠️ ver nota abajo |
| **M5** | Privilegio **demasiado ancho**: cualquiera en `_box_queue` | `:2752-2757` | **solo T1** | C1, C2, C3 verdes | ✅ modo de fallo (a) sujeto |
| **M6** | Quitar el privilegio de `acquire`, dejarlo en `enqueue` | `:297` | **solo T2** | T1 + resto | ✅ simétrico de M2 |
| **M7** | Romper el pass-through del `hint` en el cliente | `control_client.py:207` | T4 | resto | ✅ |
| **M8** | Quitar la exposición del `hint` al modelo | `server.py:709-710` | T4 | resto | ✅ |
| **M9** | Quitar `retry_after_s` del payload | `session_coordination.py:2776` | T3 | resto | ✅ |
| **M10** | Quitar el privilegio de `cancel_operation` | `:801` | **NADA** | todo | ❌ **MAYOR-1** |
| **M11** | Quitar la rama del **lease activo** del helper | `:2753-2754` | **NADA** | todo | ❌ **MAYOR-2** |

### Nota sobre M4: la alarma del brief es un falso positivo suyo

El brief predecía que al invertir el privilegio debían caer los dos del claimer
**y seguir verdes los tres de caracterización**, y que si caían los de
caracterización era señal de "privilegio mal encajado".

Cayeron C1 y C2 — pero **no por eso**. Capturé las trazas
(`scratchpad\mutate2.py`):

```
ERROR: test_tombstone_capacity_fences_unseen_operations_and_recovers_after_ttl
  test_session_acquire_wait.py:104  ->  KeyError: 'error'
ERROR: test_tombstone_saturation_does_not_block_cleanup_of_admitted_operation
  test_session_acquire_wait.py:132  ->  KeyError: 'error'
FAIL: test_saturated_error_tells_stranger_to_wait_then_retry
  test_session_acquire_wait.py:187  ->  Tuples differ: (202, None) != (503, 'operation_tombstones_saturated')
```

Las tres caen en el `enqueue` **del extraño**, que devuelve 202 en vez de 503.
Invertir el predicado no sólo le quita el pase al dueño: **se lo regala a todo
extraño**, y C1/C2/C3 son precisamente los tests que afirman que un extraño cobra
503. Esa mutación *tiene* que romperlos, esté el privilegio bien o mal encajado.
La predicción del brief es insatisfacible por construcción.

La mutación que sí responde a la pregunta ("¿está el privilegio mal encajado /
demasiado ancho?") es **M5**, y ahí el resultado es el que el brief quería: cae
sólo T1 y **los tres de caracterización se quedan verdes**. El privilegio está
bien encajado.

---

## 3. Los tres modos de fallo del spec §6

### (a) Privilegio demasiado ancho — **CERRADO**

- **Código:** `_admission_privileged_locked` (`session_coordination.py:2752-2757`)
  mira `_active.client` y `_box_claiming`. No menciona `_box_queue`.
- **Sonda propia** (la que pedía el brief: extraño con ticket de caja pero sin
  claim, a 128 tombstones):
  ```
  owner box_claimed     = True
  stranger box_ticket   = 'id-2'   (está en _box_queue: True)
  stranger box_claimed  = False
  -> enqueue(stranger)  = 503 operation_tombstones_saturated   FENCED
  -> acquire(stranger)  = 503 operation_tombstones_saturated   FENCED
  ```
- **Mutación M5:** al ensanchar el privilegio a `_box_queue`, cae T1 y sólo T1.
  El guardián vive en un único bloque, `tests\test_session_acquire_wait.py:152-156`.

### (b) Privilegio a medias entre `acquire` y `enqueue` — **CERRADO**

Es el modo que más importa (`session_acquire_wait` va por `enqueue`) y es el que
mejor queda medido, porque las dos mutaciones son discriminantes en los dos
sentidos:

- **M2** (quitar de `enqueue:720`) → cae **sólo T1**, T2 sigue verde.
- **M6** (quitar de `acquire:297`) → cae **sólo T2**, T1 sigue verde.

Que ninguna de las dos arrastre a la otra prueba que T1 y T2 no son el mismo test
escrito dos veces: cada uno sujeta su camino. Grep confirma los dos call-sites
(`:297`, `:720`) más el de `cancel_operation` (`:801`).

### (c) La receta no llega al modelo — **CERRADO**

La cadena tiene tres eslabones y **cada uno tiene su mutación en rojo**:

| Eslabón | Línea | Mutación | Cae |
|---|---|---|---|
| El coordinador emite el `hint` en el body | `session_coordination.py:2777-2780` | M3 | T3 |
| El cliente lo extrae del 503 HTTP | `control_client.py:202,207` | M7 | T4 |
| El servidor lo expone en el `ToolError` | `server.py:709-710` | M8 | T4 |

Body medido contra el coordinador real:
```
status=503  keys=['capacity','count','error','hint','retry_after_s']
hint='wait 120s then retry session_acquire_wait; do not spin'
retry_after_s=120.0
```
Y `str(ToolError)` deja de ser el código desnudo (T4 lo afirma con
`assertNotEqual(message, "operation_tombstones_saturated")`).

---

## 4. ¿Llega algún test por un atajo?

**Los tres del coordinador (T1, T2, T3): no.** Verificado leyendo el cuerpo:

1. Los 128 tombstones se acuñan con `self.coordinator.cancel_operation(...)` en
   bucle, afirmando `status == 200` en cada vuelta. **No** hay escritura directa a
   `_operation_tombstones`.
2. El claim se toma con `box_wait_touch(owner, claim=True)` y se afirma
   `claimed.get("box_claimed")`. **No** hay asignación directa a `_box_claiming`.
3. Se entra por `enqueue` / `acquire` públicos.

T1 añade además un control negativo que el spec no pedía (el waiter W en la cola
de caja recibe 503), y es justo lo que sujeta el modo de fallo (a).

**T4 (capa cliente): tiene dos atajos, ambos declarados por el implementador y
ambos legítimos.**

- Sustituye `request_with_refresh` para fabricar el 503 HTTP. Correcto: el camino
  de producción que se quiere medir empieza *después*, y sí se recorre entero
  (`_request_once` → `_decode_body` → `ControlClientError(hint=)` →
  `_control_with_lazy_spawn` → `public_error_code` → `ToolError`). Lo confirman
  M7 y M8: si rompes cualquiera de esos dos eslabones reales, T4 se pone rojo.
  Un test que hubiera construido el `ControlClientError` a mano no habría caído
  con M7.
- `patch.object(type(runtime._control.policy), "revalidate")` para que el fixture
  frozen no muera antes del parseo. Es un artefacto del fixture, no producción.

**No hay ningún test verde que no mida nada.** Cada uno de los cuatro tiene al
menos una mutación que lo pone rojo.

---

## 5. Ficheros realmente cambiados vs los que dice el informe

Diff por sha256 de los 892 ficheros de `baseline.csv` (`scratchpad\hashdiff.py`).

**Cambiados: exactamente 4. Faltantes: 0.**

| Fichero | bytes antes → después |
|---|---|
| `dayz_mcp\control_client.py` | 30 177 → 30 452 |
| `dayz_mcp\server.py` | 129 713 → 129 774 |
| `dayz_mcp\session_coordination.py` | 146 165 → 147 187 |
| `tests\test_session_acquire_wait.py` | 16 103 → 21 104 |

**Coincide con el informe en los dos sentidos: sin discrepancia.**

Añadidos fuera del venv: `DayZ_MCP_dev\README.md` y `tools\_broker\*` (los dos
que `BASELINE.md` documenta como arreglos de la copia, no del implementador) y
los tres `.bak-agujero1` que el propio encargo pedía.

**Los `.bak-agujero1` son byte-idénticos al baseline** (sha256 verificado), así que
los diffs de esta revisión salen del árbol original, no de una reconstrucción.

### Lo que no se podía tocar — todo intacto

| Cosa | Comprobación |
|---|---|
| `MAX_OPERATION_TOMBSTONES = 128` | `session_coordination.py:44`, sin cambio |
| `OPERATION_TOMBSTONE_TTL_S = 120.0` | `:45`, sin cambio |
| Código `operation_tombstones_saturated` | intacto, sigue en `_REMOTE_ERROR_CODES` (`server.py:118`) |
| 503 al extraño unseen | medido: 503 en `enqueue` y `acquire` |
| `_box_tombstones` / `box_queue_saturated` | `:1867-1872` fuera del diff |
| `loopback.py`, `process_lifecycle.py`, `daemon.py` | sha256 **idéntico al baseline** |
| Los 11 ficheros `.c` del puente | sha256 idéntico, cero desviaciones |
| Formato de `coordination.json` | `_snapshot_payload_locked` fuera del diff |

### Los tres tests de caracterización: mismo cuerpo, no sólo verdes

El diff del fichero de tests contra el árbol original (localicé dos copias
intactas con el hash del baseline: la de OneDrive y la de `mcp-laneFENCE-20260818`)
es **puramente aditivo**: 2 imports (`json`, `OPERATION_TOMBSTONE_TTL_S`) y 4
métodos nuevos. **Ni una línea de los tests existentes fue tocada.** No hay assert
ablandado, ni skip, ni renombre.

### Números de la suite

Reproduje la suite entera yo mismo: **`Ran 1626 tests` / `OK (skipped=6)`**, cero
cabeceras `FAIL:`/`ERROR:`. Coincide con `green.log`.

### El `red_first.log` es honesto — lo repliqué

No me fié del log. Restauré los tres `.bak-agujero1` (producción **original**,
hash-idéntica al baseline) dejando los tests nuevos puestos, y corrí el módulo:

```
Ran 17 tests in 2.377s
FAILED (failures=4)
   test_box_claimer_enqueue_is_not_fenced_when_tombstones_saturated       FAIL
   test_box_claimer_acquire_with_new_operation_id_is_not_fenced_when_saturated  FAIL
   test_saturated_error_tells_stranger_to_wait_then_retry                 FAIL
   test_session_acquire_wait_toolerror_includes_tombstone_recipe          FAIL
```

Exactamente los 4 nuevos en rojo y los 13 restantes en verde — incluidos los tres
de caracterización. Ninguno de los cuatro nacía verde.

Las citas `path:line` del informe se verificaron contra el árbol restaurado y son
correctas: `_admission_privileged_locked` en `:2752`, call-sites en `:297`, `:720`,
`:801`.

---

## 6. Hallazgos

### MAYOR-1 — El privilegio de `cancel_operation` no lo sujeta ningún test de los 1626

`session_coordination.py:797-802`, la tercera edición de producción:

```python
            if (
                key not in self._operation_tombstones
                and len(self._operation_tombstones) >= MAX_OPERATION_TOMBSTONES
                and not operation_admitted
                and not self._admission_privileged_locked(client)   # <-- línea 801
            ):
                return 503, self._tombstone_saturated_error_locked()
```

**Medida (M10):** borré la línea 801 y corrí **la suite entera**:
`Ran 1626 tests in 274.393s` / `OK (skipped=6)`, cero `FAIL:`/`ERROR:`.

**Y no es una línea inerte** — la sonda demuestra que cambia comportamiento real:

```
cancel_operation(claimer, id unseen) = 200 {'cancelled': True, ...}   -> acuña el 129.º
cancel_operation(extraño, id unseen) = 503 operation_tombstones_saturated
```

Corroboración independiente por grep: `operation_tombstones_saturated` sólo
aparece en `tests\test_session_acquire_wait.py` en todo el árbol de tests, así que
mecánicamente ningún otro módulo puede sujetarlo.

**Por qué importa:** es exactamente el patrón que este proyecto ya se comió dos
veces, sólo que del otro lado. El siguiente que refactorice el guard de
`cancel_operation` puede borrar esa línea y la suite le dará verde. El spec la
diseñó (§3, "si el cliente es privilegiado y el id es unseen, también deja pasar")
pero no pidió test para ella, y el implementador tampoco la listó en su §7 de "lo
que no verifiqué" — sólo mencionó la rama del lease.

### MAYOR-2 — La mitad "lease activo" de la invariante tampoco tiene test

`session_coordination.py:2753-2754`. La invariante comprada dice literalmente
"el claim de la caja **o el lease activo**". Sólo la primera mitad tiene test.

**Medida (M11):** quité la rama del lease del helper. Módulo: **17/17 OK**.
Suite entera: **`Ran 1626 tests in 213.568s` / `OK (skipped=6)`, cero
cabeceras `FAIL:`/`ERROR:`**. Igual que M10: la producción se puede borrar y la
suite no se entera.

**Y la rama es portante** — con ella fuera, el holder del lease vuelve a cobrar
exactamente el bug que se compró arreglar (sonda tomada con la mutación puesta):

```
helper cargado:  (sólo queda la rama de _box_claiming)
holder activo = 200 status=active
tombstones = 128
enqueue(holder, id nuevo) = 503 operation_tombstones_saturated
acquire(holder, id nuevo) = 503 operation_tombstones_saturated
```

El implementador **sí lo declaró** en su §7 ("Privilegio por lease activo … con un
test propio: los cuatro tests del spec cubren claim de caja"), así que es honesto,
pero sigue siendo la mitad de la invariante sin medir. Los 4 tests del spec §5 no
la cubrían; el spec §5 es quien se quedó corto.

### MEDIO-1 — `public_error_code` deja el `hint` por delante de la allowlist

`server.py:706-715`:

```python
        def public_error_code(error: ControlClientError) -> str:
            if error.code == "lease_required":
                return LEASE_REQUIRED_RECIPE
            if error.hint:                    # <-- 709, insertado por este cambio
                return str(error)             # <-- 710
            if (
                error.code in _REMOTE_ERROR_CODES
                or error.code in _CONTROL_CLIENT_ERROR_CODES
            ):
                return error.code
            return "remote_error"
```

El `if error.hint` se evalúa **antes** de la allowlist. Cualquier respuesta no-2xx
del demonio que traiga una clave `hint` devuelve al modelo `f"{code}: {hint}"` con
el código **crudo**, saltándose el saneado que convierte lo desconocido en
`remote_error`, y con el texto del `hint` literal (F1.4 existe para que no crucen
rutas del host). Es fail-open por construcción (G6): la puerta se abre por la
*presencia de un campo*, no por una lista de códigos autorizados.

**Hoy no está explotado, y lo medí en vez de suponerlo.** Grep de productores de
`"hint"` y traza de cada uno:

| Productor | ¿Llega a `ControlClientError`? |
|---|---|
| `session_coordination.py:2777` (el nuevo) | Sí — es el caso previsto |
| `session_coordination.py:1871` `box_queue_saturated` | **No**: sale por `box_wait_touch`, que `loopback.py:1735-1752` sirve en la rama por defecto con `status = 200`; `_request_once` sólo lanza si el status no es 200/202 |
| `process_lifecycle.py:240,265,294` y `server.py:2100` | **No**: son campos de un resultado de tool, no de una excepción |

Así que el radio de explosión actual es cero. Es deuda latente, no una regresión
viva. **Arreglo de una línea si se quiere cerrar:** mover el `if error.hint` a
dentro de la rama de la allowlist, es decir devolver `str(error)` sólo cuando
`error.code` ya está autorizado.

### MENOR-1 — "el 129.º" del spec es en realidad crecimiento sin techo

El spec §3 aceptó que un privilegiado pueda acuñar "el 129.º". Medido, el número
no se para en 129:

```
tras 500 cancel del claimer: tombstones = 628 (tope nominal 128)
```

Un cliente privilegiado en bucle de `session_acquire_wait` con timeout corto
acuña un tombstone por vuelta sin tope, hasta que el TTL de 120 s los purga. No es
un DoS serio (entradas de dict pequeñas, y el extraño ya estaba cercado en ≥128,
así que no empeora su situación), y está sancionado por el spec — pero el spec lo
describe en singular y no lo es. Vale la pena que quien lea el spec lo sepa.

### MENOR-2 — El modo de fallo (a) cuelga de un único bloque de asserts

`tests\test_session_acquire_wait.py:152-156` es la única cosa en el árbol que
detecta un privilegio ensanchado a `_box_queue` (medido con M5: cae T1 y nadie
más). Si alguien "simplifica" ese bloque por parecer accesorio al nombre del test,
el modo (a) se queda sin red.

### Observación — no hay test que una el payload real con el `ToolError`

T3 mide el body que produce el coordinador; T4 mide el cliente contra un body
escrito a mano en el test. Nada afirma que los dos coincidan en forma. En la
práctica la costura aguanta porque ambos lados fijan el literal `"hint"` y las
mismas subcadenas (`session_acquire_wait`, `do not spin`), así que un cambio de
nombre de clave rompería T3. No es un hallazgo, es una costura que conviene
conocer.

### Riesgo que el implementador dejó sin verificar: cerrado por medida

Su §7 dudaba de "consumidores que comparen `str(error)` en vez de `.code`" al
añadir `: {hint}`. Lo grepeé: **los cinco manejadores de `ControlClientError` en
`control_client.py` (`:410`, `:463`, `:478`, `:497`, `:513`, `:755`) usan
`.code`, ninguno `str(error)` ni `args[0]`**. Sin consumidor roto en el árbol.

También comprobé el fail-open obvio del helper (`_box_claiming` y `session_id`
ambos `None` → todo el mundo privilegiado): **inalcanzable**, porque
`ClientIdentity.from_payload` (`session_coordination.py:79-85`) rechaza un
`session_id` que no sea `str` no vacío.

---

## 7. Lo que NO pude verificar y por qué

- **Runtime.** No hay juego, servidor ni puente. Nada de lo medido acredita
  comportamiento in-game.
- **El venv.** `baseline.csv` no incluye `.venv-mcp`, así que no puedo diffear
  `site-packages` contra un estado previo. Lo que sí comprobé es que el sello
  aguanta: `__editable___dayz_mcp_tools_0_0_0_finder.py` mapea `dayz_mcp` a
  `...\mcp-laneA1-20260819\...\tools\dayz_mcp`, la **copia**, no OneDrive. Mis
  medidas importaron el árbol correcto.
- **El árbol real de OneDrive.** Sólo lo leí (para localizar una copia intacta del
  fichero de tests y confirmar su hash). No escribí ni un byte. No re-fotografié
  los 541 ficheros: eso es del orquestador.
- **`retry_after_s` con reloj real.** Todas las sondas usan `FakeClock` en 0, así
  que siempre sale `120.0`. La aritmética de `_oldest_tombstone_retry_after_locked`
  (`:2758-2767`) la leí y tiene los clamps a `[0, TTL]`, pero no la ejercité con un
  reloj avanzando.
- **Concurrencia.** No medí el helper bajo hilos en paralelo. Se llama siempre con
  el lock tomado (los tres call-sites están dentro de `with self._condition`), lo
  cual es coherente con el sufijo `_locked`, pero es lectura, no medida.
- **Los flakes de carga.** No vi ningún rojo de `DaemonStartupElectionProcessTest`
  en mis dos corridas completas, así que no tuve que triarlos.

---

## 8. Resumen para quien decida

La entrega hace lo que dice: la invariante se cumple, los tres modos de fallo del
spec §6 están cerrados con mutación en rojo cada uno, no se tocó nada de lo
prohibido, los tres tests de caracterización conservan su cuerpo original, y los
ficheros cambiados son exactamente los cuatro que el informe declara.

Lo que falta no es corrección, es **red**: dos líneas de producción (`:801` y
`:2753-2754`) sobreviven intactas a la suite entera. Cerrarlas cuesta dos tests
pequeños y del mismo estilo que los cuatro que ya hay:

1. Claimer de caja hace `cancel_operation` de un `operation_id` unseen a
   capacidad → 200; el extraño en la misma situación → 503.
2. Holder del lease activo hace `enqueue` con `operation_id` nuevo a capacidad →
   no 503 (hoy da 409 `operation_conflict`); el extraño → 503.

Y, si se quiere cerrar MEDIO-1, mover el `if error.hint` de `server.py:709` a
dentro de la rama de la allowlist.
