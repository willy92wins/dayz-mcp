# Revisión adversarial R9 — marker old-or-new

## Veredicto

GREEN

R9 cierra el último bloqueante de R8. El marker final se publica antes de autorizar cualquier artifact de datos; cada revisión es old-or-new; `marker.next` nunca constituye provenance por sí solo; un marker final válido permite rollback exacto y un receipt final exacto permite roll-forward. Los gates ahora cubren los syscalls, chunks, replaces, cleanups y un segundo crash durante recovery. No queda una ventana de crash cooperativo sin clasificación segura.

## Evidencia contrastada

- R9 crea y verifica `marker.next`, publica únicamente el marker mediante replace atómico bajo el lock de migration y exige revisión monotónica más SHA esperado del marker anterior (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:77`).
- La creación inicial publica el marker final antes del primer byte de backup. Si el crash ocurre antes del replace, cualquier temp parcial carece de autoridad y ningún artifact de datos estaba autorizado (`plan:77`).
- Receipt final exacto es autoridad de commit; marker restante se limpia idempotentemente. Sin commit, el marker final conserva provenance para rollback (`plan:78`).
- Marker final corrupto o con path/hash/revision ajenos y artifacts sin provenance continúan fail-closed, sin borrar datos (`plan:78`).
- Los gates inyectan fallo en cada chunk/syscall/replace/cleanup del marker inicial y sus updates, stale temp y segundo recovery (`plan:79,856`).
- R8 ya había fijado recovery bajo lock/quiescence, validación byte-exacta de source/artifacts, compatibilidad legacy y rollback pre-R8 (`plan:72-75`).
- La elección permanece dentro de `run_daemon`, con lock global por `RuntimePaths.root`, bind+activation+status antes de release y códigos 0/75 según health real (`plan:858`).
- V24 exige una sola generación, cubre clientes pre-R7, launch directo, mixed-version, cross-port, muerte en cada frontera, PID reuse y una segunda ola que necesariamente progresa sin drift (`plan:860`).
- Task 5B incluye tanto `identity_migration.py` como `daemon.py` y sus suites focales (`plan:842-850`).

## Tabla adversarial de autoridad

| Marker final | `marker.next` | Artifacts | Clasificación R9 |
|---|---|---|---|
| ausente | ausente/parcial/completo | ninguno | no hubo autorización de datos; limpiar temp bajo quiescence y empezar |
| ausente | cualquiera | backup/pending presentes | sin provenance; conflict, cero deletes |
| válido, revisión N | ausente/parcial N+1 | propios parciales/exactos | marker N sigue siendo autoridad; clasificar y rollback byte-exacto |
| válido, revisión N+1 | ausente | estado acorde a N o N+1 | old-or-new; clasificar artifacts, no inferir por memoria del proceso muerto |
| válido | cualquiera | receipt final + backup exactos | commit autoritativo; roll-forward y cleanup idempotente |
| válido | cualquiera | source/artifact distinguiblemente distinto | conflict, cero deletes |
| corrupto/ajeno | cualquiera | cualquiera | conflict, preservar evidencia |
| ausente | stale temp | receipt final + backup exactos | commit final autoritativo; validar y limpiar temp |

Esta tabla mantiene dos propiedades necesarias:

1. Un temp nunca autoriza delete, rollback ni commit.
2. Después de publicar el marker final, siempre queda una fuente durable de provenance hasta que el receipt final exacto asume la autoridad de commit.

## Races revisadas

- **Loser durante segundo quiescence:** el backup puede quedar parcial/completo, pero el marker final previo demuestra la operación. El retry revierte o completa según bytes exactos; ya no deriva a `incomplete_runs_backup_artifacts` ambiguo.
- **Crash durante marker inicial:** replace no ocurrió y no se autorizó backup, o ocurrió y el marker final es válido. No existe estado legítimo “backup propio sin marker”.
- **Crash durante phase update:** final conserva revisión anterior o nueva. El temp parcial/completo no suplanta al final.
- **Replace con resultado ambiguo:** recovery lee el estado durable; no depende del retorno recordado por el proceso muerto.
- **Crash durante rollback:** marker final permanece hasta verificar ausencia de artifacts; el segundo recovery repite de forma idempotente.
- **Crash durante roll-forward/cleanup:** receipt final exacto conserva autoridad aunque marker/temp permanezcan.
- **Cliente viejo:** puede lanzar N hijos, pero todos cargan el `run_daemon` nuevo; uno obtiene el lock global y los demás salen sin migration.
- **Daemon nuevo directo:** participa en la misma elección. Un daemon legacy no participante sigue fail-closed como writer real.
- **Dos puertos, mismo root:** comparten elección y autoridad persistente; no pueden activar dos coordinadores sobre `runs.json`/`coordination.json`.
- **Owner muerto:** el SO libera el lock; V24 exige que la siguiente ola recupere y publique una generación cuando no hay drift externo.

## Invariantes obligatorios durante implementación

No son cambios al diseño; concretan requisitos ya presentes en R9:

- El SHA esperado debe compararse con los bytes actuales del marker final **bajo el migration lock** antes de publicar la siguiente revisión. `os.replace` aporta publicación atómica, no debe describirse como CAS del filesystem.
- El marker/next debe usar schema cerrado, paths derivados localmente y revisiones adyacentes; recovery no confía en paths suministrados por el archivo para decidir qué borrar.
- Marker inicial se confirma parseable/hash-exacto antes de crear backup. Marker final se elimina únicamente después de verificar rollback completo o commit final exacto.
- Backup, pending receipt y marker temp se crean con exclusividad o se clasifican antes de truncar. Un artifact preexistente nunca se adopta solo por nombre.
- Write-all rechaza cero progreso/short write; cada file flush, replace, re-read y cleanup tiene error estable y preserva el último estado autoritativo.
- La prueba de marker drift debe incluir cambio entre preparación de `marker.next` y publicación. Al estar bajo el lock cooperativo, cualquier SHA/revisión distinta aborta sin replace ni deletes.
- “Status acredita generación” significa que el endpoint responde con la generación esperada; bind o activation aislados no autorizan liberar el startup lock.

## Viability y compatibilidad

V23 mantiene clasificación fail-closed de argv e identidad. Step 2b cubre tamaños menor/igual/mayor, partial writes, stale temp, publication y recovery reentrante (`plan:852-856`). V24 prueba la composición real con elección, migration, bind y activation (`plan:858-860`).

La compatibilidad queda cerrada:

- receipt pre-R8 válido continúa aceptándose;
- backup legacy sin marker no se borra ni adopta;
- binario pre-R8 acepta el commit final legacy-compatible;
- transacción R9 incompleta vista por binario antiguo falla cerrada y preserva evidencia;
- `runs.json` no cambia de formato (`plan:74`).

## Riesgos residuales aceptados

- Un daemon legacy persistente puede impedir el arranque hasta drenar; es fail-closed deliberado porque no participa en la elección.
- Drift externo distinguible requiere intervención y conserva artifacts. No se sacrifica integridad para forzar liveness.
- Un corte de energía con semántica de storage distinta a la garantizada por file flush/rename del filesystem queda fuera de los process-crash gates; no altera la corrección frente a muerte de procesos que motivó R9.
- El puerto no ganador en una prueba cross-port queda unavailable de forma explícita; una sola autoridad global es el contrato actual, no una regresión.

## Conclusión

GREEN

R9 está listo para implementación de Task 5B. La elección daemon-side global resuelve los clientes vivos y launches directos; el protocolo marker/receipt recupera cada frontera de crash cooperativo sin borrar drift externo; y V23/V24 contienen criterios verificables suficientes. No se modificó producción durante esta revisión.
