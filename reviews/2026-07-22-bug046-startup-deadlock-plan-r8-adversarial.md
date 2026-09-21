# Revisión adversarial R8 — recovery transaccional del backup

## Veredicto

BLOCKED

R8 corrige el bloqueante de R7 en la ruta de datos: introduce provenance durable antes del backup, recovery byte-exacto, pending receipt, roll-forward, compatibilidad legacy y progreso obligatorio tras la muerte del ganador. Sin embargo, no define cómo crear y actualizar de manera crash-safe el propio `runs-backup-transaction.json`. Un write/replace parcial del marker o de una transición de phase puede dejar inválida la única evidencia que autoriza borrar o completar artifacts. La política fail-closed convertiría entonces un crash cooperativo sin drift externo en conflicto permanente.

## Correcciones R8 validadas

- La transacción se arma antes del primer byte de backup y registra source path/metadata, destinos y phase sin copiar contenido de `runs.json` (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:72`).
- Recovery corre bajo lock y después de quiescence; source exacto permite retirar únicamente artifacts propios, mientras drift de source/path/schema/artifact hace cero deletes (`plan:73`).
- Receipt pre-R8 sigue siendo válido, backup legacy huérfano no se borra y rollback pre-R8 falla cerrado ante una transacción incompleta (`plan:74`).
- Un loser solo devuelve 0 tras health acreditado; sin health devuelve 75 (`plan:75,854`).
- V24 exige progreso de la segunda ola después de matar al ganador, salvo drift externo deliberado (`plan:75,856`).
- `tools/dayz_mcp/daemon.py` ya figura en el scope de Task 5B (`plan:840-846`).
- Task 5B conserva allowed-current exacto, doble quiescence, hashes y receipt, y añade fallos tras cada chunk/fsync de artifacts (`plan:848-852`).

Estas decisiones cierran los hallazgos HIGH/MEDIUM de R7 si la metadata transaccional permanece siempre clasificable.

## Hallazgo bloqueante

### HIGH-01 — el marker mutable no tiene protocolo old-or-new ante crash

R8 dice que cada phase “se persiste/fsync antes de avanzar” (`plan:72`) y V24 prueba fallo **después** de marker/phase (`plan:852`). No especifica la escritura del marker en sí: creación, cada chunk, flush, publicación/reemplazo, restos temporales ni recuperación si el proceso muere en esas fronteras.

Interleavings problemáticos:

1. **Creación inicial parcial:** el proceso crea `runs-backup-transaction.json`, escribe solo un prefijo y muere. No se ha tocado backup, pero el siguiente proceso ve schema inválido. Según R8, schema/path ajeno falla cerrado y no borra (`plan:73`); nunca progresa pese a no existir drift externo.
2. **Phase torn:** marker válido en `backup_writing` se reescribe a `backup_fsynced`; el write queda truncado/corrupto. El backup puede ser completo y exacto, pero recovery ya no puede demostrar provenance ni elegir rollback/roll-forward.
3. **Temp no clasificado:** si se implementa intuitivamente temp + rename, un crash antes/después del rename puede dejar marker anterior, marker nuevo y/o temp. Sin nombres vinculados a transaction id y una tabla exacta, el temp puede bloquear o ser borrado sin provenance.
4. **Cleanup/publicación:** final receipt puede ser exacto mientras el último update/clear del marker queda a medias. El roll-forward prometido depende de poder reconocer ese marker o de tener una regla segura basada en final backup+receipt exactos.

Impacto: **degradation de liveness persistente** y posible pérdida de provenance. El marker es la raíz de confianza de los deletes de recovery; no puede depender de una escritura in-place que a su vez requiera el marker para recuperarse.

Corrección requerida `[DESIGN]`:

- Publicar el marker inicial mediante archivo temporal único creado con exclusividad, write-all, flush y rename/replace atómico; no avanzar a artifacts hasta confirmar el marker final parseable y exacto.
- Actualizar phases con CAS old→new y semántica **old-or-new**: temp único ligado al transaction id, contenido cerrado, write-all+flush, publicación atómica y re-read/verify. Alternativamente, usar journal append-only con records autocontenidos/checksum y regla explícita para tail parcial.
- Definir la tabla recovery para: marker absent, marker old válido, marker new válido, ambos marker+temp, solo temp, temp parcial, marker corrupto y final receipt exacto. Solo los estados demostrablemente propios pueden limpiarse; drift distinguible sigue en conflict sin delete.
- La creación del marker debe comprobar antes que no existan marker, backup final, pending receipt ni final receipt incompatibles. Los creates de artifacts deben ser exclusivos; antes de publicar final receipt se revalida que el destino siga absent o sea exactamente el commit esperado.
- Tras eliminar marker/temp, verificar ausencia. Si el clear falla después de un commit exacto, conservar estado cleanup-pending recuperable; no repetir backup ni invalidar el receipt.

La solución puede reutilizar el patrón transaccional old-or-new ya exigido para config en el propio plan, pero debe quedar expresamente incorporada a Task 5B; “persistir y fsync” por sí solo no cubre un crash durante persistencia.

## Gates que faltan en Step 2b

Step 2b debe añadir inyección tras **cada syscall/chunk de metadata**, no solo después de sus estados lógicos:

1. marker temp create, cada chunk, flush y rename;
2. cada transición de phase: temp create/chunks/flush/rename/re-read;
3. crash con marker old + temp new completo/parcial;
4. crash tras final receipt exacto y durante update/clear del marker;
5. error/short-write/cero progreso en marker y pending receipt;
6. temp/marker preexistente con transaction id, schema, path, hash o phase distinto;
7. final receipt que aparece entre preflight y publicación;
8. recovery repetida y segundo crash durante rollback/roll-forward/cleanup.

Para todos los fixtures cooperativos sin drift, la siguiente ola debe publicar exactamente una generación y dejar un receipt final válido, marker/temp ausentes y backup byte-exacto. Los fixtures externos distinguibles deben conservar todos sus bytes, devolver conflict estable y hacer cero deletes.

## Observaciones no bloqueantes

- El current gate escribe backup antes del segundo `_assert_quiescent` y rechaza backup sin receipt (`tools/dayz_mcp/identity_migration.py:350-375,390-403`). El marker/recovery R8 es una corrección necesaria, no complejidad opcional.
- Un backup legacy sin marker debe seguir bloqueando: no hay forma segura de atribuirlo a R8 (`plan:73-74,852`).
- Bytes externos indistinguibles de los artifacts esperados son semánticamente equivalentes solo si paths canónicos, transaction id, source exacto y estado completo coinciden; cualquier diferencia observable queda fail-closed.
- El lock de startup debe continuar retenido hasta que status acredite la generación, como exige `plan:854`; activation por sí sola no basta si el servidor aún no responde.
- En el caso cross-port/same-root, V24 debe observar una autoridad healthy en el puerto ganador y unavailable explícito en el segundo, nunca dos coordinadores compartiendo `runs.json`.

## Condición para GREEN

R8 puede recibir GREEN cuando el plan defina la persistencia old-or-new y la recovery del propio marker/phase, e incorpore los gates de metadata anteriores. El resto de la arquitectura R8 —elección daemon-side global, losers no residentes, recovery de artifacts, compatibilidad legacy y códigos 0/75— queda aprobado.

## Conclusión

BLOCKED

La transacción de artifacts ya tiene la dirección correcta, pero su fuente de provenance aún no es transaccional. Hasta que `runs-backup-transaction.json` y sus cambios de phase sean crash-safe y su tabla de recovery cubra marker/temp torn, R8 puede volver a dejar cero daemon por un crash cooperativo sin drift. No debe modificarse producción todavía.
