# Revisión adversarial independiente — plan R10

## Veredicto

BLOCKED

R10 resuelve correctamente la carrera no-clobber de R3 para Windows y el marker `prepared` inmutable conserva provenance suficiente para **transacciones R10 nuevas**. No obstante, el plan aún no autoriza cambios por tres bloqueantes:

1. **HIGH:** el parser/gate omite la gramática compacta agrupada que CPython 3.14 ejecuta realmente, incluida una forma writer de DayZ MCP;
2. **MEDIUM:** la regla de compatibilidad exige `marker.next` legacy completo y deja permanentemente bloqueado un estado R9 legítimo con `marker.next` parcial;
3. **MEDIUM:** el paso ejecutable Task 5B sigue ordenando marker mutable R9 y `os.replace`, en contradicción directa con el addendum R10.

Hay además un hueco MEDIUM de rollback: un binario R9 no puede consumir un commit R10 que haya caído después de publicar receipt y antes de retirar el marker `prepared`. Es fail-closed, no corruption, pero R10 debe documentar y probar el procedimiento de rollback.

No se modificó producción ni tests. El único archivo creado es este informe.

## Snapshot revisado

| Archivo | Líneas | SHA-256 |
|---|---:|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | 1009 | `C377089957E4C57CBFEE12C5A536DB80D6B203403CB95221DBEF7C9EC3046895` |
| `reviews/2026-07-22-bug046-startup-deadlock-implementation-r3-adversarial.md` | 142 | `AD795D36A2069C1AD808F9C98F75D3CBAF9322F630C16651F64A801824F4E698` |
| `tools/dayz_mcp/identity_migration.py` | 1167 | `C43E284C25C196A5E376DF6E84BCE3262ADC3BF49062E5DF5F94E11E8D81AB9F` |
| `tools/tests/test_identity_migration.py` | 596 | `694CF949498744E049FAFF6E0151ECE14E4D8863FAB61B94B16545CA6FD241C1` |
| `tools/tests/test_bug046_startup_deadlock.py` | 635 | `0473C4942B96A5EBF80B97878F907E5431296B40300F5587AF1EBA186B3685F7` |
| `C:/Python314/Doc/html/library/os.html` | 6522 | `C176CBD959F7D033DDE830BE5A93824E2FDDD4B6FA5A3D41ECE72FD5E3A93C69` |

El hash del plan se verificó antes y después de la revisión y permaneció estable.

## 1. Provenance inmutable R10

### Veredicto aislado: GREEN para transacciones R10 nuevas

La sustitución del marker mutable por un marker final inmutable `prepared/revision=1` es coherente y más simple (`plan:82-89`):

- el marker se publica y revalida antes del primer artifact de datos;
- sin receipt, marker + source exacto + prefixes exactos de backup/pending permiten rollback conservador;
- con receipt final exacto, la validación del backup permite roll-forward;
- el marker se elimina el último durante rollback y después del receipt durante commit, así que un segundo crash conserva provenance o un commit final autosuficiente;
- las phases mutables no aportaban una autorización adicional: marker + bytes derivados ya distinguen los estados propios observables.

Matriz R10 nueva que sí es recuperable:

| Marker | Backup | Pending | Receipt | Recovery seguro |
|---|---|---|---|---|
| ausente; `next` prefix inicial | ausente | ausente | ausente | verificar prefix, retirar temp, reintentar |
| `prepared` exacto | ausente/parcial/completo propio | ausente/parcial/completo propio | ausente | verificar source y prefixes, rollback con marker último |
| `prepared` exacto | completo exacto | ausente | final exacto | roll-forward, retirar marker |
| ausente | completo exacto | ausente | final exacto | receipt legacy/final autosuficiente |
| ausente/corrupto/ajeno | cualquier data artifact sin receipt final válido | cualquiera | ausente | conflict, cero deletes |

No encontré un estado de crash **R10 puro** que pierda provenance si se implementa esa matriz literalmente, se mantiene create-exclusive para artifacts y todo drift distinguible se preserva. Los estados byte-a-byte iguales a un prefix propio siguen siendo observacionalmente indistinguibles, el mismo límite ya aceptado por R9.

## 2. `os.rename` Windows no-clobber

### Veredicto aislado: GREEN para el TOCTOU R3; corregir la afirmación de atomicidad

La elección de `os.rename(marker.next, marker)` en Windows (`plan:87`) sí evita el overwrite reproducido en R3. La documentación local oficial de Python 3.14 dice que, en Windows, un destino existente siempre produce `FileExistsError` (`C:/Python314/Doc/html/library/os.html:3105-3112`). El probe real con el Python aprobado confirmó:

```text
dst externo antes de os.rename:
  FileExistsError winerror=183
  src == b"ours"
  dst == b"external"

dst externo creado justo dentro de la frontera de syscall:
  FileExistsError winerror=183
  src == b"ours"
  dst == b"external-inside-syscall-boundary"
```

Por tanto, la operación falla sin backup/receipt y conserva los dos lados para clasificación. Esto cierra exactamente MEDIUM-02 de la revisión R3.

La frase de R10 “la garantía verificada de Python 3.14 es atómica y no-clobber” es demasiado amplia. El no-clobber Windows sí está documentado; la frase de atomicidad de la misma página aparece dentro del párrafo Unix y la atribuye al requisito POSIX (`C:/Python314/Doc/html/library/os.html:3113-3120`). Microsoft expone por separado `MOVEFILE_REPLACE_EXISTING` y `MOVEFILE_WRITE_THROUGH` (`C:/Program Files (x86)/Windows Kits/10/Include/10.0.26100.0/um/winbase.h:6220-6223`), pero esto no convierte el texto de Python en garantía de durabilidad Windows.

**Corrección requerida `[DESIGN]`:** decir “publicación Windows no-clobber de una sola syscall, misma carpeta/volumen” y dejar old/new bajo process-crash como criterio del gate, sin prometer power-loss durability no demostrada. Mantener fail-closed fuera de Windows; en POSIX `os.rename` sí puede reemplazar un archivo destino.

## 3. Compatibilidad/provenance R9 legacy

### Veredicto aislado: BLOCKED

### MEDIUM-01 — `marker.next` parcial R9 es un estado propio legítimo que R10 no acepta

R10 permite retirar `marker.next` junto a marker sólo si “sus bytes son **exactamente** el marker inicial o la revisión R9 adyacente esperada” (`plan:88`). Esto excluye una frontera R9 expresamente soportada:

1. R9 publica marker revision 1 `prepared` (`tools/dayz_mcp/identity_migration.py:1089-1097`).
2. Escribe backup completo (`:1099-1103`).
3. Empieza `_advance_transaction_marker` a revision 2 (`:1105-1112`).
4. `_publish_transaction_marker` crea `marker.next` mediante write-all antes del replace (`:762-804`).
5. El proceso muere tras escribir `k` bytes, con `0 <= k < len(revision_2)`.

El estado durable es:

```text
marker      = revision 1 completa y válida
backup      = source completo y exacto
marker.next = prefix propio parcial de revision 2
receipt     = ausente
```

R9 lo reconoce mediante prefix exacto esperado; de hecho la implementación actual reconstruye la revisión adyacente y llama `_assert_owned_prefix` (`tools/dayz_mcp/identity_migration.py:976-988`). R10 exigiría igualdad completa, de modo que una actualización desde un crash legítimo queda en conflict permanente. El mismo contraejemplo existe para marker revision 2 + prefix parcial de revision 3.

**Impacto:** **degradation de liveness en upgrade/recovery**. No corrompe datos porque falla cerrado, pero impide arrancar el daemon justo después del tipo de crash que R9 se diseñó para recuperar.

**Fix requerido `[DESIGN]`:** cerrar una tabla por revisión:

- marker R9 rev1 + `next` ausente o prefix byte-exacto de la rev2 reconstruida: propio;
- marker R9 rev2 + `next` ausente o prefix byte-exacto de la rev3 reconstruida: propio;
- marker R9 rev3 + cualquier `next`: conflict, salvo un estado adicional demostrado;
- marker R10 prepared + `next` prefix del marker inicial puede representar publicación inicial fallida; si también coincide con prefix legacy adyacente, tratarlo por la misma regla indistinguible;
- cualquier byte no-prefix: conflict y cero deletes.

El gate de compatibilidad debe inyectar corte tras cada byte/chunk de `marker.next` legacy, no limitarse a markers finales revision 1/2/3 (`plan:89`).

### MEDIUM-02 — rollback a R9 después de commit R10 puede quedar bloqueado

Contraejemplo:

1. R10 conserva marker revision 1 `prepared` inmutable.
2. Publica backup y receipt final exactos.
3. Muere antes de retirar el marker.
4. Se revierte el binario a la implementación R9 actual.

R9 considera receipt autoritativo, pero si marker existe exige phase `receipt_pending`; un marker `prepared` produce `runs_backup_recovery_conflict` (`tools/dayz_mcp/identity_migration.py:897-922`). El commit es válido y seguro, pero el daemon antiguo no puede arrancar.

Esto no invalida la provenance R10 ni causa overwrite: es un rollback **fail-closed**. Sí incumple R5 si queda sin documentar el comportamiento de datos post-cambio bajo rollback.

**Fix requerido `[DESIGN]`:** documentar y probar una de estas postcondiciones antes de desplegar:

- rollback operativo exige ejecutar primero recovery R10 hasta dejar receipt válido sin marker; o
- el binario R9 de rollback recibe previamente una compatibilidad puntual para receipt exacto + marker prepared exacto.

Si se revierte directamente tras el crash, el runbook debe decir que R9 preservará todo y fallará cerrado, y que hay que restaurar R10 para completar cleanup. No declarar rollback transparente.

## 4. Parser CPython y gates

### Veredicto aislado: BLOCKED

### HIGH-01 — CPython agrupa flags antes de `c/m`; R10 sólo cubre compactos sin prefijo

R10 corrige `-cCODE`, `-mMODULE`, la opción larga con valor y terminales conocidas (`plan:84-85,89`), pero no especifica la agrupación real de opciones cortas sin argumento. CPython 3.14 acepta que `c` o `m` aparezcan dentro del mismo argumento después de flags como `-B`, `-I`, `-O`, `-q` o `-bb`.

Probes exactos con `tools/.venv-mcp/Scripts/python.exe`:

```text
-bbmdayz_mcp.__main__ --help  -> exit 0; carga el parser real dayz_mcp
-Imhttp.server --help         -> exit 0; carga http.server ajeno
-OOmjson.tool --help          -> exit 0; carga json.tool ajeno
-Bcprint(321)                 -> exit 0; ejecuta el command string
```

Esto fuerza una decisión que R10 no cierra:

- si el parser conserva el fail-closed actual para el argumento desconocido, `-Imhttp.server` sigue siendo falso blocker y MEDIUM-01 de R3 no queda cerrado;
- si aplica literalmente “no fail-closed ante opción global ajena” y descarta el argumento, `-bbmdayz_mcp.__main__` puede quedar invisible, reabriendo el escape writer HIGH de R2.

**Impacto:** **safety/liveness race**. La forma agrupada DayZ puede alcanzar embedded/default sin entrar al startup election; la forma agrupada ajena puede impedir indefinidamente crear el daemon y la cola.

**Fix requerido `[DESIGN]`:** especificar el tokenizer CPython completo para Python 3.14:

- consumir una secuencia de flags cortos sin valor;
- si dentro del mismo argumento aparece `c`, el resto es command string y termina el parse de opciones;
- si aparece `m`, el resto es module target y se clasifica exactamente (`dayz_mcp`, `dayz_mcp.__main__` o ajeno);
- si aparece `W`/`X`, el resto o el siguiente argumento es valor de esa opción y no un target;
- terminal `h/?/V` termina sin writer;
- `--check-hash-based-pycs` consume exactamente un valor permitido;
- `--` fija el siguiente argumento como script target;
- opciones inválidas de Python 3.14 terminan el proceso y no deben convertirse en blockers duraderos.

Gates mínimos reales:

| argv | Esperado |
|---|---|
| `-bbmdayz_mcp.__main__ --embedded ...` | blocker |
| `-Imdayz_mcp --client ...` | no blocker sólo con gramática client exacta |
| `-OOmhttp.server ...` | no blocker |
| `-Bc<code inocuo duradero>` | no blocker |
| `-BWignore <script ajeno>` y `-BXdev <script ajeno>` | no blocker |
| `--check-hash-based-pycs default -mdayz_mcp.__main__ ...` | blocker |
| `--check-hash-based-pycs default -Imhttp.server ...` | no blocker |

Cada gate debe lanzar un proceso real y comprobar inclusión/ausencia de su PID en `scan_dayz_mcp_processes`, no sólo probar el helper privado.

## 5. Contradicción del plan ejecutable

### MEDIUM-03 — Task 5B todavía manda implementar R9 mutable con `os.replace`

El addendum dice que R10 **sustituye** las revisiones mutables R9 (`plan:86-89`). Sin embargo, el paso que el implementador debe ejecutar sigue diciendo:

> “RED/GREEN recovery R9: marker old-or-new ... atomic replace ... cada phase ... updates”

en `plans/2026-07-22-bug046-lease-queue-liveness-plan.md:865`.

Además, Task 5B Step 1 sólo fija la matriz R5/R6 (`:861`), no los compactos/agrupados R10, y V24 todavía llama al recovery “R8” (`:869`). Un ejecutor que siga el task literal reintroduciría exactamente `os.replace` y las revisiones que R10 prohíbe.

**Impacto:** **spec ejecutable contradictoria**. R20/R22 impiden elegir por iniciativa cuál de dos instrucciones incompatibles obedecer.

**Fix requerido `[EXACT]`:** sustituir Step 1/2b, no dejarlo como nota histórica activa:

- Step 1 RED V23/R10: matriz completa CPython separada/compacta/agrupada y procesos reales;
- Step 2b RED/GREEN recovery R10: marker inmutable, `os.rename` Windows no-clobber, cero updates R10, tabla exacta R10 + legacy R9 incluida `next` parcial;
- Step 4 V24: segunda ola usa recovery R10 y compatibilidad R9;
- conservar tests R9 old-or-new como fixtures legacy, no como algoritmo del writer nuevo.

## Gates R11 requeridos

R11 puede obtener GREEN sin volver a marker mutable si incorpora estos criterios verificables:

1. Parser real con flags agrupados antes de `c/m/W/X`, positivos DayZ y negativos ajenos por PID.
2. Publicación Windows `os.rename`: destino externo creado dentro de la syscall queda byte-exacto, `next` propio permanece, no aparecen backup/receipt y el error es conflict estable.
3. Crash R10 en cada chunk de initial `next`, antes/después de rename, revalidación, cada chunk/fsync de backup/pending, receipt publish y cleanup; segunda ejecución progresa.
4. Matriz legacy: marker R9 rev1/rev2 con `next` parcial en cada byte, `next` completo adyacente, rev3 sin next, receipt legacy y artifacts drift; propios progresan, ajenos se preservan.
5. Rollback: fixture que ejecuta recovery R9 sobre receipt R10 + marker prepared y confirma el comportamiento documentado, seguido del procedimiento R10 que limpia/progresa.
6. Task 5B reescrito sin instrucciones activas R9/`os.replace` contradictorias.

## Validación ejecutada

- Lectura completa del Addendum R10 y de Task 5B activa.
- Reconstrucción de estados R9/R10 contra `identity_migration.py` actual.
- Probe Windows de `os.rename` con destino preexistente y destino creado dentro de la frontera de syscall.
- Probes CPython 3.14 de `-bbmdayz_mcp.__main__`, `-Imhttp.server`, `-OOmjson.tool` y `-BcCODE`.
- Verificación de documentación local Python 3.14 y constantes Windows SDK.

No se ejecutaron suites porque esta es revisión de plan y los contraejemplos son previos a cualquier implementación R10. Todos los probes fueron read-only salvo archivos temporales autocontenidos eliminados al cerrar `TemporaryDirectory`.

## Conclusión

BLOCKED

- **Provenance inmutable R10 nueva:** GREEN aislado.
- **`os.rename` Windows frente al TOCTOU R3:** GREEN aislado para no-clobber.
- **Compatibilidad legacy/rollback:** BLOCKED por `marker.next` parcial y rollback R9 no especificado.
- **Parser/gates:** BLOCKED por targets `c/m` agrupados.
- **Plan ejecutable:** BLOCKED porque Task 5B aún ordena R9 mutable + `os.replace`.

R11 debe conservar el marker inmutable y `os.rename`; no hace falta volver a la complejidad R9. Debe cerrar el tokenizer agrupado, la matriz legacy parcial, el rollback y reemplazar las instrucciones activas de Task 5B. Sólo entonces procede otra revisión con posibilidad de GREEN.
