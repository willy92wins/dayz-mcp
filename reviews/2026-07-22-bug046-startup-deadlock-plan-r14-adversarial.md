# Revisión adversarial independiente — plan R14

## Veredicto

BLOCKED

R14 cierra correctamente los tres bloqueantes de R13: limita la paridad al target canónico, autoriza el cambio de dispatch y parser en GREEN, y sustituye la tabla manual por un builder `argparse` compartido con clasificación silenciosa. Queda una contradicción ejecutable en los gates: el parser compartido debe clasificar como writer el client sin `--keyfile`, mientras el gate heredado todavía exige que esa misma forma compacta no bloquee.

## Alcance e integridad

- Plan revisado: `plans/2026-07-22-bug046-lease-queue-liveness-plan.md`.
- Snapshot estable durante la revisión: 1.018 líneas, SHA-256 `2847AC1DB72DC9B9BC14B17D76D1734F6C084B662D6A414DAF57EFAC5803B89A`.
- No se editó producción ni tests. Sólo se creó este informe.
- `tools/dayz_mcp/server_cli.py` no existía en el snapshot revisado, como corresponde al estado RED previo a R14.

## Cierres correctos de R14

### Alcance canónico y narrowing

`plan:86,92-93,870-872` ya define una sola política:

- la paridad del tail se aplica sólo a `-m dayz_mcp`, incluidas las formas compactas/agrupadas que resuelven exactamente ese module target;
- `-m dayz_mcp.__main__` y script directo permanecen fail-closed para cualquier tail;
- Step 2 autoriza cambiar tanto dispatch como helper y actualizar las expectativas antiguas;
- README debe documentar el reinicio con el entrypoint canónico.

Esto resuelve la imposibilidad de implementación de R13. El narrowing también es compatible con el estado vivo observado mediante scan agregado y redactado: 52 procesos canónicos, 52 client-only, 52 con `--keyfile`, cero `module_main`, cero scripts directos y cero flags abreviados. No se mostró ningún argv ni valor.

### Fuente única para semántica CLI

El nuevo diseño de `plan:92` es superior a duplicar una tabla: `server_cli.py` contiene las opciones y tipos una sola vez; `server.parse_args` conserva el parser CLI, mientras el scanner obtiene un resultado cerrado `client|writer|invalid|help` sin importar FastMCP ni emitir salida.

El contrato cubre las diferencias verificadas de `argparse`:

- `--idle-timeout -1` es client válido.
- `--task-label=-nightly` es client válido.
- `--task-label -nightly` es inválido.
- `--task-label --daemon --client` es inválido.
- `--keyf` es inválido con `allow_abbrev=False`.
- client repetido es válido; modos distintos mezclados son inválidos.

El parse silencioso debe implementarse mediante parser/action o excepción privada capturada, suprimiendo también la ruta `print_help`; no debe usar redirección global temporal de `sys.stdout/sys.stderr`, porque podría interferir con otro thread. El resultado exigido y el gate de cero salida de `plan:872` son suficientes para comprobarlo en la revisión de implementación.

### TDD y scope

`plan:870` es ahora RED puro. `plan:872` autoriza expresamente crear `server_cli.py`, conectar `server.parse_args`, fijar `allow_abbrev=False`, cambiar dispatch y actualizar los tests antiguos. Esto cierra la contradicción Step 1/Step 2 de R13 sin ampliar arquitectura.

### Provenance

La matriz rev1/rev2 de `plan:89,874` permanece intacta: marker rev1 acepta exactamente prefix de `rev2_expected`, con `common-1/common/common+1`; no reaparece la ambigüedad de R12.

## Hallazgo bloqueante

### MEDIUM-01 — El gate compacto sin `--keyfile` contradice parse-invalid → writer

**Evidencia `[EXACT]`:**

- `server.py:1248` declara `parser.add_argument("--keyfile", required=True)`.
- `plan:86,92` exige que todo tail canónico inválido permanezca writer/fail-closed.
- `plan:870` usa correctamente procesos reales con `-m dayz_mcp --client --keyfile K` y una forma agrupada equivalente.
- Sin embargo, `plan:94` aún exige que `-Imdayz_mcp --client` **no bloquee**, sin aportar el `--keyfile` obligatorio.

**Impacto:** el mismo argv tiene dos resultados normativos. Si el parser compartido se implementa correctamente, `-Imdayz_mcp --client` devuelve invalid y el PID bloquea, fallando `plan:94`. Si se añade una excepción para hacer pasar el gate heredado, el scanner deja de compartir la semántica CLI y rompe el fail-closed de `plan:92`.

Un test puede esconder la contradicción usando un `K` implícito, pero el gate está descrito como argv concreto y las revisiones anteriores demostraron que las omisiones de tail generan precisamente falsos resultados.

**Corrección requerida `[EXACT]`:** cambiar en `plan:94`:

- de `-Imdayz_mcp --client`
- a `-Imdayz_mcp --client --keyfile K`.

Renombrar además el encabezado de esa línea a “Gates R14” o declarar expresamente que `plan:870` sustituye la matriz R13 completa.

## Observaciones no bloqueantes

- `plan:95` dice revisar el drift provisional contra R13 después del GREEN. La spec vigente será R14; debe decir “contra R14” para no preservar código provisional que incumpla el parser compartido o narrowing.
- La cita `plan:91` atribuye la construcción de `ArgumentParser` a `server.py:1247-1285`, pero en el snapshot actual la construcción está en `server.py:1246` y `--keyfile` en `:1248`. Ajustar el rango a `:1245-1296` restauraría cite-then-verify.
- `plan:94` conserva el rótulo “Gates R13”, aunque su contrato ya depende de R14. Es cosmético una vez corregido el argv, pero conviene evitar que el ejecutor lo trate como evidencia histórica no normativa.

## Drift procedural observado

Durante esta revisión volvieron a cambiar archivos provisionales externos, sin intervención de este revisor:

- `identity_migration.py`: de 1.388/SHA `DDA9A188...` a 1.450/SHA `F671B6D0...`.
- `test_identity_migration.py`: de 671/SHA `1EA9C848...` a 721/SHA `E667CAEA...`.
- `test_client_mode.py`: de 759/SHA `429B7C55...` a 814/SHA `A522D85F...`.
- `test_bug046_startup_deadlock.py`: de 828/SHA `47466389...` a 947/SHA `89211981...`.

`server.py` permaneció en 1.336/SHA `5EAAB370...` y `server_cli.py` seguía ausente. `plan:95` reconoce el drift provisional, pero el escritor concurrente debe detenerse hasta un GREEN literal; después se congelan hashes y se revisa cada línea contra R14 antes de preservar nada.

## Validación realizada

- Rehash del plan al inicio y al cierre.
- Verificación del parser real, obligatoriedad de keyfile, orden `parse_args` antes de `build_app/run_daemon` y dispatch actual.
- Probes read-only previos de `argparse` para negativos, equals, duplicados, help, abreviaturas y tails inválidos.
- Scan vivo agregado/redactado de forma de entrypoint, modo, presencia de keyfile y abreviaturas; cero mutaciones y cero terminaciones.
- No se ejecutó la suite global: es revisión de plan y la contradicción está en los criterios, antes del código.

## Gate requerido para R15

R15 puede recibir GREEN si:

1. corrige el gate compacto para incluir `--keyfile K`;
2. etiqueta la matriz como R14 y cambia la revisión del drift de R13 a R14;
3. conserva sin cambios builder único, parse silencioso sin output, invalid → writer, help → no-writer, narrowing canónico y matriz rev1/rev2;
4. mantiene detenido cualquier cambio de producción/tests hasta ese GREEN y luego congela el snapshot provisional.

## Conclusión

BLOCKED

La arquitectura R14 ya es coherente y soluciona los tres hallazgos de R13. La única contradicción funcional restante es local: un gate omite el `--keyfile` obligatorio y exige no-blocker donde el parser compartido debe devolver invalid/writer. Corregido ese argv y los dos rótulos stale, el plan queda listo para una revisión R15 corta.
