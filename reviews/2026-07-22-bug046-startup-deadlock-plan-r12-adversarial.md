# Revisión adversarial independiente — plan R12

## Veredicto

BLOCKED

R12 cierra correctamente los dos bloqueantes de R11 en su intención: `W/X` reanudan el scan con positivos DayZ, e initial-next sólo se atribuye a R10 cuando marker está ausente. Sin embargo, quedan dos bloqueantes de liveness/provenance en los criterios ejecutables:

1. los encodings rev1 y rev2 comparten un prefix de 187 bytes; el plan acepta todos los prefixes rev2 y rechaza todos los prefixes initial, por lo que prescribe resultados opuestos para el mismo `marker.next` observable;
2. la gramática DayZ posterior al target todavía excluye clientes válidos y duraderos con valores negativos o forma `--option=value`; dos procesos reales fueron clasificados como blockers.

No se modificó producción ni tests por esta revisión. El único archivo creado es este informe.

## Snapshot revisado

| Archivo | Líneas | SHA-256 |
|---|---:|---|
| `plans/2026-07-22-bug046-lease-queue-liveness-plan.md` | 1011 | `2A7312C02559A688B0A77791969EDFCD2EF3E66583DD23049D220E065B65304A` |
| `reviews/2026-07-22-bug046-startup-deadlock-plan-r11-adversarial.md` | 169 | `B72815C7A942846F58609DAAB5EB9B00A5D23672A39ACEF6FF289E05E22170A9` |
| `tools/dayz_mcp/identity_migration.py` | 1388 | `DDA9A18825862D2A1BE07AF890DF087A4CEB634D986A142ECF97F0EA216F9CEE` |
| `tools/dayz_mcp/server.py` | 1338 | `FE323FE4DB5B53C55A7423E7FFE43E3E84E9B1AFE0679EDF2CA8A30F53A5F5F6` |
| `tools/tests/test_identity_migration.py` | 671 | `1EA9C84833DDD86AA2C43DBC2EEF6D0DE0EBB2A62082EB0E1252C1C8F7B2ACC1` |
| `tools/tests/test_bug046_startup_deadlock.py` | 828 | `474663898B12658F0B3BDE11A80DD19C5F345A75FDC12C6D371B58C3B79FBA35` |

El plan conservó el mismo hash antes y después de la revisión.

### Drift procedural observado

Durante esta revisión de plan, producción/tests ya no conservaban el snapshot de R11:

- `identity_migration.py`: 1167→1388 líneas; `C43E...`→`DDA9...`;
- `test_identity_migration.py`: 596→671; `694C...`→`1EA9...`;
- `test_bug046_startup_deadlock.py`: 635→828; `0473...`→`4746...`.

Estos cambios no fueron hechos por este revisor. Contradicen el gate del plan “ninguna modificación de producción antes de GREEN” (`plan:13,91`). La futura revisión de implementación debe tratarlos como un snapshot nuevo no autorizado por R12, congelar sus hashes y no confundir su existencia con un GREEN de plan.

## Cierre de bloqueantes R11

### R11 MEDIUM-01 — initial-next bajo marker presente

**CERRADO en la partición de estados.** R12 coloca initial-next prefix bajo marker ausente y sin data artifacts; con marker rev1 presente sólo admite ausencia/prefix rev2 (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:89,867`). Esto refleja correctamente `os.rename`: una publicación R10 exitosa mueve `next`, no deja ambos nombres.

Permanece una intersección byte-a-byte contradictoria entre ambos conjuntos de prefixes, detallada en MEDIUM-01 R12.

### R11 HIGH-01 — early return en `W/X`

**CERRADO.** El tokenizer dice expresamente que `W/X` consumen valor y continúan en el siguiente argv sin retornar (`plan:86`). Los gates positivos prueban module y script DayZ posteriores a `W/X`, tanto separados como agrupados (`:91,863`). Un early-return ya no puede pasar la suite.

### Nits R11

**CERRADOS.** `h/V` salieron del conjunto continue, stdin `-` tiene resultado explícito y los negativos usan `<foreign-script>` (`plan:86,91,863`).

## Hallazgos R12

### MEDIUM-01 — prefixes initial/rev2 solapados hacen contradictoria la matriz rev1

**Evidencia:** con marker rev1 presente, R12 prescribe:

- aceptar “cualquier prefix byte-exacto de rev2” (`plan:89,867`);
- rechazar “rev1 + prefix del marker inicial” y preservar ambos (`:89,91,867`).

Pero los dos encodings JSON no divergen en el primer byte. Usando los builders/encoder reales y los mismos paths/metadatos:

```text
len(initial rev1) = 512
len(rev2)         = 580
common_prefix     = 187 bytes
first divergence:
  rev1 -> b'prepared",\n  "previous_sha256": null...'
  rev2 -> b'backup_written",\n  "previous_sha256": "A371...'
```

Por tanto, para todo `0 <= k <= 187`, incluido un `marker.next` vacío tras create/crash:

```text
initial.startswith(initial[:k]) is True
rev2.startswith(initial[:k])    is True
```

El mismo archivo debe ser simultáneamente “propio legacy: limpiar/progresar” y “initial drift: conflict/preservar”. Ninguna implementación puede satisfacer ambos gates para todos los prefixes.

**Impacto:** **degradation de liveness o provenance**, según la precedencia improvisada. Rechazar el prefix común rompe recovery de un crash R9 legítimo; aceptar todos los initial-prefix sin condición amplía provenance a bytes que divergen de rev2 y que ningún writer cooperativo deja junto a rev1.

**Fix requerido `[EXACT]`:** clasificar por el conjunto permitido observable, con regla any-match:

- marker ausente + cero data: `initial_expected.startswith(actual_next)` autoriza cleanup;
- marker rev1 presente: `rev2_expected.startswith(actual_next)` autoriza cleanup, aunque esos bytes también sean prefix initial;
- el negativo rev1+initial sólo es conflict cuando `initial_expected.startswith(actual_next)` **y** `not rev2_expected.startswith(actual_next)`;
- elegir en el gate negativo un corte después del primer byte divergente, no recorrer el prefix común esperando rechazo;
- rev2/rev3 conservan las reglas ya escritas.

Añadir tres asserts alrededor de la frontera: `k=common-1`, `k=common` aceptan como rev2; `k=common+1` tomado de initial rechaza/preserva.

### MEDIUM-02 — clientes DayZ válidos con valores negativos/`--opt=value` siguen siendo falsos writers

**Evidencia de contrato real:** `server.parse_args` acepta `--idle-timeout` como `float` y `--task-label` como string (`tools/dayz_mcp/server.py:1247-1264`). Probes directos confirmaron:

```text
parse_args(['--client','--keyfile','K','--idle-timeout','-1']).idle_timeout_s == -1.0
parse_args(['--client','--keyfile','K','--task-label=-nightly']).mode == 'client'
```

Ambas formas arrancan el proxy stdio client y permanecen vivas esperando stdin; no abren/migran `runs.json`.

El clasificador que Task 5B Step 2 modificaría sigue definiendo una gramática más estrecha: cualquier argumento con `=` es writer y cualquier value que empiece por `-` es writer (`tools/dayz_mcp/identity_migration.py:447-487`, en particular `:472-476`). R12 no añade gates para la cola DayZ posterior al module target; sólo cubre el tokenizer CPython previo (`plan:86,91,863`).

**Reproducción de proceso real:** se lanzaron dos hijos client con stdin abierto y keyfile temporal:

```text
['--idle-timeout', '-1']  alive=True  child_pid_in_blockers=True
['--task-label=-nightly'] alive=True  child_pid_in_blockers=True
```

Los PIDs exactos fueron observados por `scan_dayz_mcp_processes`; ambos hijos terminaron limpiamente al cerrar su stdin.

**Impacto:** **degradation de liveness bloqueante**. Un cliente perfectamente válido puede permanecer vivo, ser tomado por writer y agotar el candidate drain, recreando el deadlock clientes-vivos/daemon-ausente que Task 5B pretende eliminar.

**Fix requerido `[DESIGN]`:** cerrar la gramática DayZ client contra `server.parse_args`, no sólo la del intérprete:

- admitir formas separadas y `--option=value` para value-options conocidas;
- permitir valores negativos donde el parser tipado real los acepta (`--idle-timeout`, y decidir/documentar `--port`);
- `--client` debe seguir siendo selector exacto y único; daemon/embedded/default/múltiple/desconocido permanecen writer;
- decidir explícitamente si se desactiva `argparse` abbreviation con `allow_abbrev=False` o si el scanner debe reconocer abreviaturas válidas. Hoy `--keyf` y `--no-daemon-auto` son aceptados por `parse_args`, otra fuente de falsos blockers;
- evitar llamar al parser con efectos/`SystemExit` dentro del scanner: extraer una gramática side-effect-free compartida o implementar una tabla cerrada equivalente y probar paridad.

Gates reales por PID mínimos:

| Tail tras `-m dayz_mcp` | Esperado |
|---|---|
| `--client --keyfile K --idle-timeout -1` | no blocker |
| `--client --keyfile=K --task-label=-nightly` | no blocker |
| `--client --port=-1 --no-daemon-autospawn` | no blocker o CLI rechazado explícitamente por validación nueva |
| `--client --keyf K` | no blocker si se conserva `allow_abbrev=True`; si no, `server.parse_args` también debe rechazarlo |
| `--client=1` | inválido; nunca confundirlo con selector client |
| `--client --daemon` / default / unknown | blocker |

## Observación no bloqueante

Task 5B V24 aún dice “segunda ola ... usando recovery R11” (`plan:871`) aunque Step 2b y el addendum son R12. No hay un segundo algoritmo de producción, por lo que es un label stale, no una ambigüedad arquitectónica. Cambiarlo a `recovery R12` antes de declarar el plan final evita que el gate reporte la revisión equivocada.

## Gates R13 requeridos

R13 no necesita rediseñar marker ni tokenizer. Basta con:

1. Reescribir la regla rev1 por any-match a rev2 y hacer el negativo initial exclusivo después de la divergencia.
2. Añadir paridad de gramática DayZ client para valores separados negativos, formas `=`, y una decisión sobre abbreviations.
3. Cambiar el label V24 R11→R12/R13.
4. Mantener todos los gates ya cerrados: W/X positivos, rename no-clobber, partial next R9, rollback R9, crash matrix y segunda ola real.

## Validación ejecutada

- Lectura completa de Addendum R12 y Task 5B.
- Comparación uno-a-uno con los hallazgos R11.
- Construcción real de encodings rev1/rev2 y medición de su common prefix.
- `server.parse_args` con valores negativos, forma `=` y abreviaturas.
- Dos procesos client reales duraderos observados por `scan_dayz_mcp_processes`.
- Revalidación del hash del plan.

No se ejecutaron suites porque es una revisión de plan y los dos contraejemplos preceden a los gates propuestos. Los procesos de probe propios se cerraron limpiamente; no se terminó ningún proceso ajeno.

## Conclusión

BLOCKED

R12 cierra W/X, stdin, terminales, initial-next ausente, rollback, `os.rename` y Task 5B R12. Todavía no puede autorizar implementación porque su matriz de prefixes prescribe dos resultados para los mismos primeros 187 bytes y porque clientes DayZ válidos aún pueden quedar clasificados como writers permanentes. Ambas correcciones son locales y deben entrar en R13 antes de otra revisión literal GREEN/BLOCKED.
