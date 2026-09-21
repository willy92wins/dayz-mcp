# Revisión adversarial independiente — implementación R3

## Veredicto

BLOCKED

R3 cierra los cuatro hallazgos de R2 y las suites focales pasan completas, incluida la recuperación real de `run_daemon` tras muerte en cuatro fronteras. Sin embargo, la revisión adversarial encontró dos bloqueantes MEDIUM nuevos:

1. el parser confunde procesos Python ajenos y duraderos con writers MCP cuando usan formas válidas compactas o una opción larga de CPython;
2. la publicación del marker todavía puede sobreescribir drift externo en la ventana entre la segunda comprobación y `os.replace`.

Ambos contradicen criterios centrales de BUG-046: el primero puede impedir que exista daemon y, por tanto, que las sesiones entren en cola; el segundo viola el contrato R9 de preservar drift externo. No se modificó producción ni tests. El único archivo creado es este informe.

## Snapshot revisado

Los hashes se comprobaron al comienzo y de nuevo después de los probes; permanecieron estables.

| Archivo | Líneas | SHA-256 |
|---|---:|---|
| `tools/dayz_mcp/identity_migration.py` | 1167 | `C43E284C25C196A5E376DF6E84BCE3262ADC3BF49062E5DF5F94E11E8D81AB9F` |
| `tools/dayz_mcp/daemon.py` | 601 | `0FD625366E7F55CD80C608E79BBFD7DF62788944A501895F4791D2A3CA563DA1` |
| `tools/tests/test_identity_migration.py` | 596 | `694CF949498744E049FAFF6E0151ECE14E4D8863FAB61B94B16545CA6FD241C1` |
| `tools/tests/test_daemon_security_gate.py` | 169 | `DD78B8AAA38F813D7C2BB2930E9745BF4D46A5A1A85A93D08DB9E42BE1F45731` |
| `tools/tests/test_bug046_startup_deadlock.py` | 635 | `0473C4942B96A5EBF80B97878F907E5431296B40300F5587AF1EBA186B3685F7` |
| `tools/tests/test_daemon.py` | 431 | `5D7E68D35C910A9F3649E5691368871B7EB63D3303BE0DEE295B2A8CDCE7962A` |
| `tools/tests/fixtures/dayz_mcp/__main__.py` | 121 | `A16F87A2DA22826532FD51BD32A741F36B0326022C5C202AF29114B097D8C826` |

## Revalidación de hallazgos R2

### R2 HIGH-01 — escape `-m dayz_mcp.__main__`

**CERRADO.** El parser obtiene el target real del intérprete y clasifica `dayz_mcp.__main__` como writer en `tools/dayz_mcp/identity_migration.py:273-337`. La matriz cubre default/embedded/client en `tools/tests/test_identity_migration.py:455-479`, y la integración observa un proceso real `-m dayz_mcp.__main__ --embedded` mediante el scanner en `tools/tests/test_bug046_startup_deadlock.py:291-333`.

Las negativas solicitadas también están: `-c`, `-m pytest` y script/linter que llevan `dayz_mcp/__main__.py` sólo como dato se ignoran en `tools/tests/test_identity_migration.py:481-495`.

### R2 MEDIUM-01 — identidad temporalmente imposible del redirector

**CERRADO.** La captura del parent obtiene PID, `create_time`, executable y argv dentro de `oneshot`, deriva ambos hashes de esas observaciones, exige parent estrictamente anterior al hijo, compara la identidad fuerte del snapshot y revalida el `ppid` en `tools/dayz_mcp/identity_migration.py:408-487`. Además, `ensure_runs_v1_backup` exige que la excepción siga siendo el parent inmediato en `:1018-1030`.

El test positivo y las negativas de parent posterior, PID reutilizado y reparenting están en `tools/tests/test_identity_migration.py:312-408`. La comparación por identidad fuerte durante cada scan permanece en `tools/dayz_mcp/identity_migration.py:490-576`.

### R2 MEDIUM-02 — owner-death sin recovery real

**CERRADO para las fronteras candidatas de R3.** El fixture de `tools/tests/fixtures/dayz_mcp/__main__.py:72-108` ejecuta el `run_daemon` real y sólo inyecta muerte propia con exit 91:

- tras `after_backup_write` de migration;
- post-bind;
- post-activation;
- post-status accreditation.

En cada subcaso, la segunda ola publica status con `daemon_generation`, termina limpia, conserva backup byte-exacto, valida receipt y elimina el marker transaccional (`tools/tests/test_bug046_startup_deadlock.py:335-409`). No mata procesos reales ni sustituye la migration en este test.

Queda una mejora de cobertura no bloqueante: el test exige una generación no vacía pero no compara `coordination.json["daemon_generation"]` con el status de la segunda ola en estos cuatro subcasos (`:397-409`). Esa consistencia sí se prueba en el wave test contiguo (`:480-493`), pero no específicamente después de cada crash.

### R2 MEDIUM-03 — inicialización de lock con progreso cero

**CERRADO.** Los dos locks exigen retorno exactamente `1`, ejecutan `fsync` y vuelven a comprobar tamaño antes de adquirir el byte-range lock (`tools/dayz_mcp/identity_migration.py:179-184,232-237`). Zero, short y fallo de fsync se prueban para startup y migration lock en `tools/tests/test_bug046_startup_deadlock.py:208-247`.

## Hallazgos R3

### MEDIUM-01 — opciones válidas de CPython ajeno se convierten en falsos writers MCP

**Evidencia de código.** `_argv_targets_dayz_mcp` sólo reconoce `-c` y `-m` cuando son argumentos separados (`tools/dayz_mcp/identity_migration.py:283-298`). Cualquier opción no enumerada que empiece por `-` devuelve `True` de inmediato (`:305-321`), antes de conocer el target real. Esto incluye sintaxis válida de CPython que no ejecuta DayZ MCP:

- `-cCODE`;
- `-mMODULE`;
- `--check-hash-based-pycs always|default|never`;
- también formas terminales como `-?`, `--help`, `--help-env` y `--version`.

Las formas terminales son cortas, pero las tres primeras pueden permanecer vivas indefinidamente. El scanner transforma el `True` en blocker sin verificar ningún target MCP (`tools/dayz_mcp/identity_migration.py:558-563`). `_assert_quiescent` lo convierte en `dayz_mcp_process_present` (`:646-671`) y el daemon sólo reintenta hasta agotar `MIGRATION_CANDIDATE_DRAIN_S` (`tools/dayz_mcp/daemon.py:134-146,451`).

**Reproducción real.** Con el Python aprobado del venv se lanzaron tres procesos ajenos duraderos y se ejecutó `scan_dayz_mcp_processes()` mientras seguían vivos. En los tres casos el PID exacto del hijo apareció en blockers:

```text
--check-hash-based-pycs  pid=42056  child_in_blockers=True
-cimport...              pid=32020  child_in_blockers=True
-mtimeit                 pid=55328  child_in_blockers=True
```

Se verificó además contra el propio CLI: `-cprint(123)` ejecutó e imprimió `123`, `-mjson.tool` cargó el módulo y `--check-hash-based-pycs default -c ...` devolvió 0. No son argv malformadas.

La suite no lo detecta porque sus negativas usan exclusivamente `-c CODE`, `-m pytest` y un script posicional (`tools/tests/test_identity_migration.py:481-495`).

**Impacto:** **degradation de liveness bloqueante**. Un servidor, worker, test runner o utilidad Python completamente ajena que use una forma compacta válida puede mantener todos los intentos de daemon fuera de servicio. Sin daemon no existe autoridad de cola, exactamente el modo de fallo que BUG-046 pretende eliminar.

**Fix requerido `[DESIGN]`:** parsear la gramática real del launcher CPython antes de aplicar fail-closed al target MCP:

- reconocer `-cCODE` como command-string ajeno igual que `-c CODE`;
- reconocer `-mMODULE` y aplicar a `MODULE` la misma clasificación exacta que a `-m MODULE`;
- consumir `--check-hash-based-pycs` y su valor;
- clasificar las opciones terminales conocidas como no-writer;
- conservar fail-closed para un target `dayz_mcp` ambiguo/desconocido, no para todo proceso Python con una opción global desconocida.

Añadir negativas de proceso real, al menos un `-mhttp.server`/`-mtimeit` duradero, `-cCODE` y `--check-hash-based-pycs ...`; conservar positivos writer para `-mdayz_mcp`, `-mdayz_mcp.__main__` y sus formas separadas. El criterio debe comprobar ausencia del PID en blockers, no sólo llamar al predicado privado.

### MEDIUM-02 — el marker externo aún puede ser sobreescrito después del segundo check

**Evidencia de código.** `_publish_transaction_marker` verifica el marker antes de preparar el temp (`tools/dayz_mcp/identity_migration.py:762-776`) y vuelve a verificarlo justo antes de publicar (`:788-797`). A continuación usa `os.replace(next_path, marker_path)` (`:798-801`), que sobrescribe un destino que aparezca después de la segunda verificación. El comentario de `:788-789` afirma detectar drift no cooperativo en la ventana exacta, pero queda un TOCTOU entre el check y la syscall.

El test existente escribe el marker externo mediante `fault_injector("marker_before_replace")` (`tools/tests/test_bug046_startup_deadlock.py:70-90`). Ese hook se ejecuta **antes** del segundo check (`identity_migration.py:786-795`), por lo que sólo prueba el lado ya cubierto de la ventana.

**Reproducción determinista.** Se interceptó exclusivamente `os.replace` para crear un marker externo justo al entrar en la syscall y luego ejecutar el `os.replace` original. Resultado:

```text
external_preserved False
final_is_ours      True
next_exists        False
```

Es decir, ambas comprobaciones habían pasado, el writer externo publicó su marker y la implementación lo eliminó silenciosamente.

**Impacto:** **corruption/safety race**. Una escritura no cooperativa o mixed-version en esa ventana pierde su prueba de transacción; los dos procesos pueden continuar con estados incompatibles. Además contradice literalmente el contrato R9: drift de marker/artifact externo debe fallar cerrado con cero deletes.

**Fix requerido `[DESIGN]`:** la publicación necesita semántica atómica de compare/no-clobber, no otra lectura previa. Para marker inicial, publicar sólo si el destino sigue ausente mediante una primitiva no-replace. Para revisiones, definir una transición recuperable que compare la identidad/contenido esperado del marker anterior y no pueda reemplazar un destino nuevo; si Windows/Python no ofrece CAS de archivo suficiente, hay que ajustar el protocolo R9 explícitamente antes de implementar. Añadir un gate situado dentro de la syscall de publicación o un writer competidor sincronizado después del segundo check, y exigir preservación byte-exacta del marker externo y cero deletes.

## Validación ejecutada

Intérprete: `tools/.venv-mcp/Scripts/python.exe`, con `PYTHONPATH=tools` y `LOCALAPPDATA` temporal aislado para cada grupo.

| Suite | Resultado |
|---|---|
| `tools/tests/test_identity_migration.py -q` | 18 tests, OK |
| `tools/tests/test_daemon_security_gate.py -q` | 7 tests, OK |
| `tools/tests/test_daemon.py -q` | 21 tests, OK |
| `tools/tests/test_bug046_startup_deadlock.py -q` | 14 tests, OK; 1 skipped por privilegio de symlink |

Total: **60 tests OK, 1 skipped**. La suite BUG-046 tardó 51.475 s. No se ejecutó suite global porque los dos bloqueantes focales ya impiden GREEN y el encargo priorizaba el veredicto R3.

Además de las suites se ejecutaron:

- matriz adversarial directa del parser;
- tres procesos CPython reales ajenos observados por el scanner;
- validación del CLI real para opciones compactas/largas;
- reproducción determinista del TOCTOU de `os.replace`.

Todos los hijos propios de los probes se cerraron al terminar. No se terminó ningún proceso ajeno ni se tocó el daemon vivo.

## Conclusión

BLOCKED

R3 corrige de forma convincente los fallos R2 y ya cuenta con un recovery multiproceso real. Aun así, no se autoriza gate vivo ni aplicación: el parser puede volver a dejar todas las sesiones sin daemon por actividad Python ajena, y la publicación del marker no cumple todavía su garantía de preservación frente a drift externo. Corregir MEDIUM-01 y MEDIUM-02, añadir sus reproducciones a la suite y repetir una R4 sobre hashes estables.
