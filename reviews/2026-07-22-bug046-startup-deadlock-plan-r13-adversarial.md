# Revisión adversarial independiente — plan R13

## Veredicto

BLOCKED

R13 corrige los dos bloqueantes de R12: la clasificación rev1 usa exclusivamente `rev2_expected.startswith(actual_next)` y la frontera compartida se prueba en `common-1/common/common+1`; además, la cola acepta las formas CLI válidas que motivaron el segundo hallazgo (`--idle-timeout -1`, `--task-label=-nightly`, `--keyfile=K` y `--client` repetido) y cierra deliberadamente las abreviaturas con `allow_abbrev=False`.

No obstante, el contrato ejecutable aún contiene dos contradicciones y una ambigüedad de paridad. Una implementación literal no puede satisfacer simultáneamente el helper, el alcance de Step 2 y los gates de entrypoint.

## Alcance e integridad

- Plan revisado: `plans/2026-07-22-bug046-lease-queue-liveness-plan.md`.
- Snapshot inicial/final de esta revisión: 1.016 líneas, SHA-256 `72A309B9ECDC4FF4C6C0EBAEAE679DB2D9E2F2A5D2216978953B08B5D8C8CE01`.
- Fuentes leídas, no modificadas:
  - `tools/dayz_mcp/identity_migration.py`: 1.388 líneas, SHA-256 `DDA9A18825862D2A1BE07AF890DF087A4CEB634D986A142ECF97F0EA216F9CEE`.
  - `tools/dayz_mcp/server.py`: 1.336 líneas, SHA-256 `5EAAB370DD2F77F7A861422ACF0791DFF1CEA931C50B4BC76B8916DE8F285708`.
  - `tools/tests/test_identity_migration.py`: 671 líneas, SHA-256 `1EA9C84833DDD86AA2C43DBC2EEF6D0DE0EBB2A62082EB0E1252C1C8F7B2ACC1`.
  - `tools/tests/test_client_mode.py`: 759 líneas, SHA-256 `429B7C550C762681A03AEF32B24178E5A9F0DEF1E12EF4CEA95F91D184FE9DAD`.
  - `tools/tests/test_bug046_startup_deadlock.py`: 828 líneas, SHA-256 `474663898B12658F0B3BDE11A80DD19C5F345A75FDC12C6D371B58C3B79FBA35`.
- No se editó producción ni tests. Sólo se creó este informe.
- Durante la cadena R12→R13, `server.py` derivó desde el snapshot previo de 1.338 líneas/SHA `FE323FE4...` al snapshot anterior. Es drift externo a esta revisión y queda cubierto proceduralmente por `plan:94`; debe congelarse justo después del futuro GREEN.

## Cierres correctos de R13

### Provenance rev1/rev2

`plan:89,872` ya no decide por el origen nominal de un prefix ambiguo. Con marker rev1, acepta exactamente cuando los bytes observados son prefix de `rev2_expected`; por tanto, los primeros bytes comunes reciben un solo resultado. `common+1` tomado del initial después del primer byte divergente preserva evidencia, mientras `common-1/common` aceptan recovery. Esto cierra el BLOCKED-01 de R12.

### Formas CLI válidas y abreviaturas

`server.py:1245-1283` prueba el parser real: value-options tipadas, `--keyfile` obligatorio y grupo mutuamente exclusivo de modo. Probes read-only contra `server.parse_args` confirmaron:

- `--keyfile K --client --client`: parsea `mode=client`.
- `--keyfile K --idle-timeout -1 --client`: parsea `mode=client`, `idle_timeout_s=-1.0`.
- `--keyfile K --task-label=-nightly --client`: parsea `mode=client`.
- Con el parser actual, `--keyf K --client` también parsea como client por la abreviatura implícita.

El cambio R13 a `allow_abbrev=False` es una decisión compatible con los consumidores observados: un scan redactado encontró cero strict-prefix flags en `.claude.json` y `.codex/config.toml`, y cero usos operativos en el repo fuera de fixtures negativos de tests. El rollback a un binario anterior vuelve a aceptar abreviaturas y no existe formato persistente nuevo. No hay bloqueante en la decisión de compatibilidad en sí.

### Tokenizer CPython y Task 5B

`plan:86,93,868` conserva correctamente consumo y reanudación para `W/X`, separa terminales `h/?/V`, contempla stdin `-` y retira `h/V` del conjunto continue. Los gates positivos/negativos evitan que un early-return en `W/X` pase inadvertido. V24 ya referencia recovery R13 (`plan:876`).

## Hallazgos bloqueantes

### HIGH-01 — La paridad no está acotada al target canónico y contradice el gate de entrypoint directo

**Evidencia `[EXACT]`:**

- `plan:92` atribuye a `_dayz_mcp_tail_is_writer` una tabla equivalente al CLI R13 sin limitarla a una forma de target.
- `plan:93,868` exige, en cambio, que `-W ignore dayz_mcp/__main__.py --client` sea blocker.
- `plan:870` ordena reemplazar **sólo el predicado** de capacidad de escritura.
- El dispatch real llama al mismo helper para `-m dayz_mcp`, `-m dayz_mcp.__main__` y el script cuyo parent se llama `dayz_mcp`: `identity_migration.py:572-586`.
- Los tests actuales acreditan justamente el comportamiento opuesto para los entrypoints alternativos: `direct_client` y `module_main_client` esperan ausencia de PID en `test_identity_migration.py:486-490`.

**Impacto:** el plan no determina una implementación posible. Si se sigue `plan:92,870` y se mejora sólo el helper, el client directo será no-blocker y falla V23. Si se hace que el helper bloquee cualquier client alternativo, falta la provenance del target dentro del helper y se puede bloquear también el target canónico. Si se cambia el dispatch para distinguirlos, se viola el alcance literal “sólo el predicado” y se cambian expectativas existentes sin que el plan lo autorice.

Además, un `python -m dayz_mcp.__main__ --client --keyfile K` es un cliente CLI real y duradero. Marcarlo fail-closed es una decisión de trust admisible si sólo `-m dayz_mcp` se considera resolución acreditada, pero reintroduce deliberadamente el deadlock clientes-vivos/daemon-ausente para ese entrypoint. Debe quedar como límite de soporte explícito, no emerger accidentalmente de un gate.

**Corrección requerida `[DESIGN]`:**

1. Declarar expresamente que R13 sustituye la cláusula tail de R12 en `plan:86` y que la paridad scanner/CLI se aplica **sólo** al target canónico exacto `-m dayz_mcp` (incluida su forma CPython compacta).
2. Autorizar en Step 2 el cambio del dispatch/callsite necesario para transportar o decidir `target_kind/target_value`; no limitarlo a `_dayz_mcp_tail_is_writer`.
3. Fijar gates separados:
   - `-m dayz_mcp --client --keyfile K` y `-Imdayz_mcp --client --keyfile K` → no-blocker.
   - `-m dayz_mcp.__main__ --client --keyfile K` → blocker si esa es la política de trust.
   - `dayz_mcp/__main__.py --client --keyfile K` → blocker si esa es la política de trust.
   - daemon/embedded/default para los tres entrypoints → blocker.
4. Actualizar deliberadamente `test_identity_migration.py:486-490` y documentar la compatibilidad: clientes alternativos existentes deben reiniciarse con el entrypoint canónico o seguirán bloqueando startup.

### MEDIUM-01 — Step 2 prohíbe el cambio de parser que R13 exige

**Evidencia `[EXACT]`:**

- `plan:91` exige construir el parser R13 con `allow_abbrev=False`.
- `plan:862,864` añade correctamente `server.py` y `test_client_mode.py` al scope.
- `plan:868`, dentro de Step 1 RED, dice “fijar `allow_abbrev=False`”.
- `plan:870`, el único Step GREEN de la clasificación, ordena “reemplazar solo el predicado”.
- La restricción global TDD prohíbe modificar producción antes del RED (`plan:13`).

**Impacto:** un ejecutor disciplinado tiene dos lecturas incompatibles: modificar `server.py` durante RED, rompiendo TDD, o no modificarlo durante GREEN, dejando abreviaturas activas y fallando el contrato R13. Incluir el archivo en la lista de scope no resuelve qué paso autoriza la línea de producción.

**Corrección requerida `[DESIGN]`:**

- Step 1 RED debe limitarse a añadir los tests que esperan rechazo de `--keyf` y paridad del corpus, observando el fallo actual.
- Step 2 GREEN debe ordenar explícitamente dos cambios mínimos: `ArgumentParser(..., allow_abbrev=False)` en `server.py` y el dispatch/predicado del scanner definido por HIGH-01.
- Mantener un gate específico que demuestre que el error de abreviatura ocurre en `parse_args` antes de `build_app`/`run_daemon`; `server.py:1310-1315` confirma el orden actual.

### MEDIUM-02 — “No rechazar valores con `-`” no es equivalente a `argparse`

**Evidencia `[EXACT]`:**

- `plan:92` dice simultáneamente que la tabla es equivalente al CLI y que no rechaza un valor conocido por comenzar con `-`.
- El parser real acepta la forma separada `--idle-timeout -1` y la forma equals `--task-label=-nightly`, pero rechaza `--task-label -nightly` con “expected one argument”.
- El parser también rechaza `--task-label --daemon --client`; no consume `--daemon` como string value.
- Una tabla que consume incondicionalmente el argv siguiente para cualquier value-option puede clasificar `--keyfile K --task-label -nightly --client` o `--keyfile K --task-label --daemon --client` como client no-writer, aunque `server.parse_args` rechaza ambos.

**Impacto:** no es un falso blocker de un cliente válido, pero sí deja indeterminado el contrato fail-closed y hace falsa la afirmación de paridad. Dos implementaciones diferentes pueden cumplir frases distintas del plan y producir resultados opuestos para tails malformadas.

**Corrección requerida `[DESIGN]`:** escoger y documentar una de estas semánticas:

- **Paridad estricta recomendada:** reconocer en forma separada sólo tokens que `argparse` puede consumir como argumento; para cualquier string que parezca opción exigir `--name=value`, excepto negativos que el parser real admite. Parser-invalid → writer/fail-closed.
- **Superset seguro por liveness:** permitir tails parser-invalid como no-writer porque `parse_args` termina antes de construir autoridad. En ese caso retirar “equivalente”/fail-closed para esas formas y demostrar explícitamente el orden `parse_args` antes de writer.

En ambos casos, el corpus debe incluir al menos: `--idle-timeout -1`, `--task-label=-nightly`, `--task-label -nightly`, `--task-label --daemon --client`, value-options repetidas, boolean-options repetidas, client repetido, modos mixtos y abreviaturas.

## Observaciones no bloqueantes

- `plan:86` aún dice que “múltiple” es fail-closed, mientras `plan:91-93,868` acepta el mismo `--client` repetido. R14 debe precisar “selectores de modo distintos/mixtos” o declarar supersesión; el criterio R13 posterior permite inferir la intención, pero no conviene dejar texto mutable incompatible.
- `allow_abbrev=False` cambia una superficie CLI existente pero no un formato persistente. La justificación de rollback en `plan:91` es suficiente; README debe mencionar que sólo nombres completos son soportados.
- Los fixtures operativos revisados usan nombres completos. Los `--daem` observados están en tests negativos de `doctor.py`, no en launchers.

## Validación realizada

- Rehash del plan antes y después de la lectura.
- Inspección de `server.parse_args`, del dispatch de `_argv_targets_dayz_mcp` y de los tests de modos existentes.
- Probes read-only del parser para client repetido, negativos, equals y abreviaturas.
- Scan redactado de strict-prefix flags en repo y registros locales; no se mostraron valores de configuración ni secretos.
- No se ejecutó la suite global porque es una revisión de plan y los conflictos preceden a la implementación.

## Gate requerido para R14

R14 puede recibir GREEN cuando:

1. limite explícitamente la paridad al target acreditado o, alternativamente, acredite y soporte los entrypoints alternativos;
2. alinee helper, dispatch, tests actuales y gates reales de PID con esa decisión;
3. separe RED de los cambios GREEN en `server.py`/scanner;
4. defina el resultado de tails parser-invalid que contienen valores con `-`;
5. mantenga la matriz rev1/rev2 y los gates `common-1/common/common+1` de R13;
6. congele hashes post-GREEN antes de tocar el drift provisional.

## Conclusión

BLOCKED

R13 cierra correctamente provenance, prefixes y los clientes canónicos con negativos/equals/duplicados, y la retirada de abreviaturas no rompe ningún launcher observado. No puede autorizar implementación todavía porque la paridad del helper contradice el gate fail-closed de entrypoints alternativos, Step 2 no autoriza el cambio de parser que exige R13 y la regla de valores con guion no define el mismo lenguaje que `argparse`. Son correcciones locales de contrato y gates; no requieren rediseñar la arquitectura.
