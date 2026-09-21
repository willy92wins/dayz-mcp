# Revisión adversarial independiente — plan R11

## Veredicto

BLOCKED

R11 corrige los tres bloqueantes principales de R10: reconoce selectores CPython agrupados, recupera `marker.next` legacy parcial por prefix, documenta el rollback R9 fail-closed y reescribe Task 5B sin writer R9 mutable ni `os.replace`. Sin embargo, quedan dos contraejemplos bloqueantes:

1. la matriz atribuye a un marker `prepared/rev1` una forma `next` R10 que ningún writer cooperativo puede dejar y que es indistinguible de drift junto a un marker R9;
2. el tokenizer dice que `W/X` consumen su valor, pero no exige continuar hasta el target siguiente y sus gates sólo son negativos. Una implementación que retorna “no-writer” al ver `W/X` pasa todos esos gates y deja invisible un writer DayZ real.

No se modificó producción ni tests. El único archivo creado es este informe.

## Snapshot revisado

| Archivo | Líneas | SHA-256 |
|---|---:|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | 1011 | `7E866A26ED324EDE8294428EF59144774034E6055E7B772A3A767445BD986477` |
| `reviews/2026-07-22-bug046-startup-deadlock-plan-r10-adversarial.md` | 236 | `E40A3152CC995A85227EFB2B03BBF3060A33A366B529944D3D895868D6D79895` |
| `tools/dayz_mcp/identity_migration.py` | 1167 | `C43E284C25C196A5E376DF6E84BCE3262ADC3BF49062E5DF5F94E11E8D81AB9F` |
| `tools/tests/test_identity_migration.py` | 596 | `694CF949498744E049FAFF6E0151ECE14E4D8863FAB61B94B16545CA6FD241C1` |
| `tools/tests/test_bug046_startup_deadlock.py` | 635 | `0473C4942B96A5EBF80B97878F907E5431296B40300F5587AF1EBA186B3685F7` |

El plan conservó el mismo hash antes y después de esta revisión.

## Cierre de bloqueantes R10

### R10 HIGH-01 — selectores `c/m` agrupados

**CERRADO en diseño base.** R11 fija un tokenizer carácter-a-carácter, distingue flags sin valor, `c`, `m`, `W`, `X` y terminales, y exige procesos reales por PID (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:85-86,91`; Task 5B `:863`). Esto cubre los contraejemplos `-bbmdayz_mcp.__main__`, `-Imdayz_mcp --client`, `-OOmhttp.server` y `-BcCODE`.

Permanece el escape posterior a `W/X`, detallado en HIGH-01 de R11.

### R10 MEDIUM-01 — `marker.next` R9 parcial

**CERRADO.** La matriz ahora reconstruye la revisión adyacente y acepta cualquier prefix byte-exacto: rev1→rev2, rev2→rev3; rev3 exige ausencia de `next` (`plan:89`). El gate recorre cada prefix/chunk (`:91`) y Task 5B lo traslada a un paso ejecutable (`:867`). Esto conserva provenance y recupera los crashes R9 legítimos.

Permanece una regla R10 imposible junto al mismo marker indistinguible, detallada en MEDIUM-01 de R11.

### R10 MEDIUM-02 — rollback R9 tras receipt R10 + marker prepared

**CERRADO.** R11 declara el outcome exacto: R9 rechaza sin deletes; se restaura R10 para completar cleanup; sólo con receipt válido sin marker se vuelve a R9 (`plan:90`). El gate ejecuta ambas mitades, no simula rollback transparente (`:90-91,867`). Cumple el requisito de explicar el comportamiento post-cambio bajo rollback y conserva los bytes.

### R10 MEDIUM-03 — Task 5B contradictoria

**CERRADO.** Step 1 es V23/R11 con argv separadas/compactas/agrupadas (`plan:863`); Step 2b ordena marker inmutable, `os.rename` Windows no-clobber y **cero** updates/`os.replace` (`:867`); V24 usa recovery R11 y artifacts R9 (`:871`). Ya no hay dos algoritmos activos incompatibles.

### Afirmación de `os.rename`

**CERRADA.** R11 limita la afirmación a no-clobber Windows, misma carpeta/volumen, falla cerrado fuera de Windows y no promete atomicidad/durabilidad no documentada (`plan:88`). El gate conserva el destino externo creado dentro de la syscall y exige cero backup/receipt (`:91,867`).

## Hallazgos R11

### MEDIUM-01 — `prepared/rev1 + next prefix initial` no tiene provenance cooperativa

**Evidencia:** R11 mantiene el marker R10 con la misma forma observable que el R9 revision 1: `prepared/revision=1` (`plan:87`). La matriz distingue semánticamente:

- “marker R9 rev1”: `next` ausente o prefix de rev2;
- “marker R10 prepared”: `next` ausente o prefix del marker inicial (`plan:89`).

No existe un campo que permita saber cuál de las dos etiquetas corresponde a un JSON `prepared/rev1` observado tras restart.

Más importante: con marker final ya presente, `next` prefix del marker inicial **no es un estado cooperativo R10**. `os.rename(next, marker)` mueve el nombre fuente:

- success: queda marker y `next` desaparece;
- `FileExistsError`: el marker ya pertenecía a otra escritura/race y la operación actual debe terminar en conflict sin data artifacts (`plan:88,91`).

R9 tampoco genera initial-next junto a rev1: su siguiente escritura después de rev1 apunta a la revisión 2. Por tanto, este estado observable:

```text
marker      = prepared/rev1 válido
marker.next = prefix del encoding initial prepared/rev1
```

no tiene provenance cooperativa. Puede producirlo exactamente un writer externo que crea el marker durante la syscall R10, o drift posterior. Aceptarlo permite que recovery retire bytes externos bajo una gramática que el propio protocolo nunca escribe.

La indistinguibilidad obliga a elegir una sola regla por estado, no dos etiquetas históricas:

- tomar la unión acepta un estado externo imposible;
- tomar sólo la rama R10 rompe `next` parcial legacy rev1→rev2;
- la intersección no es necesaria y perdería liveness legacy.

**Impacto:** **safety/provenance degradation**. La corrección no toca `runs.json`, pero reabre cleanup de un artifact externo después de que R3/R10 exigieran preservar drift en la frontera de publicación.

**Fix requerido `[EXACT]`:** separar por existencia del marker, no por una versión imposible de observar:

- marker **ausente**, data artifacts ausentes: `next` ausente o prefix del marker inicial R10 se puede retirar/reintentar;
- marker **presente** rev1: `next` ausente o prefix de la rev2 R9 reconstruida;
- marker presente rev2: `next` ausente o prefix de rev3;
- marker presente rev3: `next` ausente;
- cualquier otra combinación: conflict y cero deletes.

Eliminar de `plan:89` la frase “Marker R10 prepared admite ... prefix del marker inicial” para el caso con marker presente. Añadir un gate negativo `marker rev1 + initial-next prefix` que preserve ambos bytes; mantener el positivo `marker ausente + initial-next prefix`.

### HIGH-01 — `W/X` pueden ocultar un target DayZ posterior y los gates no lo detectan

**Evidencia:** el tokenizer especifica que `W/X` consumen el sufijo o el argumento siguiente como valor y nunca como target (`plan:86`). No dice explícitamente que, después de consumir ese valor, debe continuar con el siguiente argv para localizar `-m` o script. Los gates sólo exigen negativos ajenos:

- `-BWignore <script>` no bloquea;
- `-BXdev <script>` no bloquea (`plan:91,863`).

Una implementación defectuosa pero conforme a esos tests puede ejecutar `return False` al encontrar `W/X`. Todos los negativos pasan.

CPython 3.14 demuestra que el target puede venir después. Probes reales con el venv aprobado:

```text
-BWignore -mdayz_mcp.__main__ --help  -> exit 0; carga dayz_mcp
-BXdev -mdayz_mcp.__main__ --help     -> exit 0; carga dayz_mcp
-W ignore dayz_mcp/__main__.py --help -> exit 0; carga dayz_mcp
-X dev dayz_mcp/__main__.py --help    -> exit 0; carga dayz_mcp
```

Las cuatro formas pueden sustituir `--help` por default/embedded y convertirse en writer real.

**Impacto:** **safety/liveness race**. El writer queda invisible al doble quiescence y puede tocar la misma autoridad persistente fuera del startup election, equivalente al HIGH de `-m dayz_mcp.__main__` ya corregido en R3.

**Fix requerido `[EXACT]`:** en `plan:86`, después de “consumen ... como valor” añadir “**y continúan el scan en el siguiente argumento; no retornan clasificación**”. Ampliar gates reales por PID:

| argv | Resultado |
|---|---|
| `-BWignore -mdayz_mcp.__main__ --embedded ...` | blocker |
| `-BXdev -mdayz_mcp --daemon ...` | blocker |
| `-W ignore dayz_mcp/__main__.py --client ...` | blocker por entrypoint directo |
| `-X dev -- dayz_mcp/__main__.py ...` | blocker |
| `-W -m dayz_mcp` | no module target: `-m` es valor de W; `dayz_mcp` es script ajeno/no existente |
| `-BWignore <foreign-script>` | no blocker |

Esto prueba tanto consumo como reanudación y evita que un early-return pase la suite.

## Observaciones no bloqueantes

- `h` aparece tanto dentro del conjunto de flags que “continúan” como entre terminales (`plan:86`). La implementación debe dar precedencia terminal a `h/?/V`; conviene retirar `h` del conjunto para que `-Bhm...` no dependa del orden de ramas. Es un falso positivo efímero, no un writer invisible, porque CPython termina mostrando ayuda.
- El gate `-BWignore <script>` debe nombrar explícitamente un `<foreign-script>`; un `dayz_mcp/__main__.py` es blocker después de consumir W. La tabla propuesta arriba elimina la ambigüedad.
- El target stdin exacto `-` no está mencionado. Debe tratarse como target no atribuible/ajeno bajo el mismo límite ya aceptado para `-cCODE`, no como una opción CPython inválida. No cambia la clasificación resultante, pero completa la gramática.

## Gates ejecutables tras la corrección

El resto de los gates R11 es suficiente y trazable:

1. Parser por PID cubre separadas, compactas, agrupadas, long option y modos DayZ (`plan:91,863`). Falta sólo la reanudación positiva tras `W/X`.
2. Rename race exige destino externo byte-exacto, conflict y cero artifacts (`:91,867`).
3. Crash matrix R10 cubre cada chunk/syscall de next, rename/revalidación, backup/pending, segundo quiescence, receipt y cleanup; segunda ejecución progresa (`:91,867`).
4. Legacy recorre cada prefix rev1→rev2/rev2→rev3 y drift; falta sólo el negativo initial-next con marker ya presente (`:89,91,867`).
5. Rollback ejecuta R9 reject/no-delete y R10 cleanup antes de volver (`:90-91,867`).
6. V24 exige una generación real tras recovery R11 y compatibilidad R9 (`:871`).

No se necesita cambiar arquitectura, reintroducir phases mutables ni abandonar `os.rename`. Las dos correcciones son locales al texto/matriz/gates.

## Validación ejecutada

- Lectura completa de Addendum R11 y Task 5B reescrita.
- Comparación uno-a-uno con todos los bloqueantes del informe R10.
- Reconstrucción de estados observables marker/next R9 y R10.
- Probes CPython 3.14 de cuatro writers reales posteriores a `W/X`.
- Revalidación de hashes tras los probes.

No se ejecutaron suites: no existe implementación R11 y los contraejemplos son del plan/gates. Todos los procesos de probe terminaron por sí mismos mediante `--help`; no se terminó ningún proceso real.

## Conclusión

BLOCKED

R11 está cerca de GREEN y conserva correctamente las dos simplificaciones valiosas: marker R10 inmutable y publicación Windows no-clobber. Para R12 basta con:

1. mover `initial-next prefix` al caso **marker ausente** y prohibirlo cuando rev1 ya existe;
2. exigir que `W/X` reanuden el scan y añadir positivos DayZ reales después de sus valores;
3. limpiar la duplicidad no bloqueante de `h` y concretar `<foreign-script>`.

Sólo después procede una revisión R12 con posibilidad de GREEN.
