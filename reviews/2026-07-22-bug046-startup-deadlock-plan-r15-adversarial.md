# Revisión adversarial independiente — plan R15

## Veredicto

GREEN

El plan es implementable sin contradicciones funcionales pendientes. R15 corrige el único blocker de R14: la forma compacta canónica ahora incluye el `--keyfile` obligatorio, por lo que el gate y el parser compartido producen el mismo resultado. Puede comenzar la implementación controlada conforme a R14, congelando primero el drift provisional como exige el propio plan.

## Alcance e integridad

- Plan revisado: `plans/2026-07-22-bug046-lease-queue-liveness-plan.md`.
- Snapshot estable al inicio y cierre: 1.018 líneas, SHA-256 `15366E78CC3FD1E6CC3F829987E4758CFBA7AD6797F17F3ADAC6D8E774228517`.
- No se modificó producción ni tests. Sólo se creó este informe.
- Este GREEN aprueba el plan, no atribuye ni aprueba automáticamente la implementación provisional externa. `plan:95` obliga a congelarla y revisarla línea a línea contra R14 antes de preservarla.

## Comprobación de los bloqueantes anteriores

### R12 — prefixes de provenance

Resuelto en `plan:89,874`:

- marker rev1 acepta exactamente `rev2_expected.startswith(actual_next)`;
- `common-1` y `common` aceptan;
- `common+1` del initial, después de la primera divergencia, preserva/conflict;
- marker ausente sólo atribuye prefix initial cuando no existen data artifacts;
- rollback R9→R10 y recovery legacy rev2/rev3 conservan gates propios.

No quedan dos resultados para los mismos bytes observables.

### R13 — target y paridad CLI/scanner

Resuelto en `plan:86,92-94,870-872`:

- la paridad se limita al target exacto acreditado `-m dayz_mcp`, incluidas formas compactas/agrupadas equivalentes;
- `-m dayz_mcp.__main__` y script directo bloquean para cualquier tail;
- el narrowing y el reinicio requerido están documentados;
- `server_cli.py` será una fuente única stdlib-only para las opciones `argparse`;
- parser CLI y parse silencioso comparten tipos, opciones, `allow_abbrev=False` y modos;
- valid client → no-writer; daemon/embedded/default/invalid → writer; help terminal → no-writer;
- el parse silencioso no produce `SystemExit`, stdout ni stderr;
- Step 1 es RED puro y Step 2 autoriza builder, parser, dispatch y actualización de expectativas.

Esto elimina tanto el drift de una tabla manual como la ambigüedad de valores que empiezan por `-`.

### R14 — gate compacto y keyfile

Resuelto en `plan:91,94,870`:

- `server.py:1245-1296` acredita que `--keyfile` es obligatorio;
- el gate no-blocker ahora es `-Imdayz_mcp --client --keyfile K`;
- no queda ninguna ocurrencia normativa de la variante compacta sin keyfile;
- client repetido también se prueba con keyfile;
- la matriz está rotulada `Gates R14`.

La forma compacta deja de exigir una excepción al parser y conserva invalid → writer.

## Matriz funcional final

| Estado observable | Resultado R14 |
|---|---|
| target canónico + CLI válido `mode=client` | no-blocker |
| target canónico + default/daemon/embedded | blocker |
| target canónico + CLI inválido/unknown/abreviado | blocker |
| target canónico + help terminal | no-blocker |
| `dayz_mcp.__main__` o script directo, cualquier tail | blocker |
| Python ajeno, `-c`, módulo ajeno, stdin o terminal CPython | no-blocker |
| bootstrap acreditado/daemon/embedded o identidad incompleta | blocker/fail-closed |
| marker rev1 + next prefix de rev2 | recovery permitido |
| marker rev1 + initial posterior a la divergencia de rev2 | preserve/conflict, cero deletes |

Cada fila tiene un gate concreto en `plan:94,870,874` y no contradice otra fila.

## Compatibilidad y estado vivo

La retirada de abreviaturas y el narrowing son explícitos y reversibles en cuanto al binario:

- configs y launchers observados usan nombres completos y `-m dayz_mcp`;
- el rollback a binario anterior vuelve a aceptar abreviaturas;
- no cambia persistencia ni payload de red;
- un scan vivo agregado/redactado durante R14 encontró 52 clientes canónicos, todos client-only y con keyfile, cero entrypoints alternativos y cero flags abreviados.

Por tanto, el cambio no introduce un blocker conocido para las 52 sesiones vivas observadas.

## Disciplina de arranque autorizada

Antes del primer patch de producción, el ejecutor debe aplicar literalmente `plan:95`:

1. congelar hashes de `identity_migration.py`, `server.py`, tests y cualquier nuevo archivo provisional;
2. comparar el drift R12-R14 línea a línea contra R14;
3. preservar sólo cambios trazables al plan y corregir con patches mínimos;
4. no atribuir cambios externos a esta sesión;
5. ejecutar los RED de `plan:870` antes del GREEN de `plan:872`.

El GREEN no relaja esa barrera ni autoriza sobrescribir trabajo concurrente.

## Validación de esta revisión

- Rehash repetido del plan: estable.
- Búsqueda de la variante compacta stale sin keyfile: cero ocurrencias normativas.
- Verificación de cita y obligatoriedad del parser.
- Revisión cruzada de addendum, Task 5B y gates de recovery/V24.
- Confirmación de que el drift se revisará contra R14, no R13.
- No se ejecutaron suites porque esta fase revisa exclusivamente el plan; la suite pertenece a la implementación después de los RED focales.

## Observación no bloqueante

`plan:62` conserva la frase histórica R5 “`--client` exacto y único”. No es una spec vigente: R5 fue bloqueada y la regla normativa posterior `plan:86,91-94,870` acepta expresamente uno o más `--client`. La secuencia de revisiones y los gates finales eliminan la ambigüedad ejecutable; puede conservarse como historia del plan.

## Conclusión

GREEN

R15 deja el plan coherente, verificable y listo para implementación. No quedan blockers de target, parser, help/invalid, keyfile, abbreviations, prefixes, recovery o gates. El siguiente paso autorizado es congelar el snapshot provisional, ejecutar los RED R14 y aplicar los cambios mínimos bajo revisión posterior independiente.
