# Revisión adversarial R7 — elección dentro de `run_daemon`

## Veredicto

BLOCKED

R7 corrige los tres bloqueantes estructurales de R6: la elección ocurre en todo daemon nuevo aunque lo lance un cliente viejo o una shell, el lock protege el `RuntimePaths.root` global y los candidatos que pierden no permanecen esperando como writers. Sin embargo, el retry de candidate drain no es recuperable con la transacción de backup actual. Un loser puede aparecer durante el segundo chequeo de quiescence, después de crear `runs.pre-v2.json` y antes del receipt. El intento siguiente encuentra el backup sin receipt y falla permanentemente como `incomplete_runs_backup_artifacts`.

## Evidencia contrastada

- R7 mueve la elección dentro de `run_daemon`, la hace global por `RuntimePaths.root` y mantiene el ownership en un handle del SO (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:68-70`). Esto sí incluye hijos de clientes ya cargados, launches directos y otros puertos.
- El ganador conserva el lock durante probe, migration, bind y activation; un perdedor hace probe y sale sin migration/bind (`plan:69,847`). Así desaparecen los bypasses client-side de R6.
- El gate de backup adquiere su lock, ejecuta un primer `_assert_quiescent`, escribe el backup y solo después ejecuta el segundo `_assert_quiescent` (`tools/dayz_mcp/identity_migration.py:350-375`).
- Si el segundo chequeo falla, no existe rollback del backup ni journal de ownership en esa ruta. El receipt se crea más tarde (`identity_migration.py:377-403`).
- En el siguiente intento, backup existente sin receipt produce inmediatamente `incomplete_runs_backup_artifacts` (`identity_migration.py:354-361`). R7 solo reintenta `dayz_mcp_process_present` y excluye errores de backup/integridad (`plan:69`).
- El flujo actual hace migration antes de bind y activation (`tools/dayz_mcp/daemon.py:350-390`), por lo que este fallo deja cero autoridad escuchando.

## Hallazgo bloqueante

### HIGH-01 — un loser transitorio puede dejar un backup propio irrecuperable

Interleaving reproducible:

1. El daemon A gana `daemon_startup_election` y entra en `ensure_runs_v1_backup`.
2. A supera el primer `_assert_quiescent` (`identity_migration.py:350-353`).
3. A escribe y fsync-ea `runs.pre-v2.json` (`identity_migration.py:363-372`).
4. Un cliente viejo lanza B. B pierde el startup lock, pero durante su breve vida su argv sigue siendo `--daemon` y el scanner lo clasifica writer-capable.
5. El segundo `_assert_quiescent` de A observa B y lanza `dayz_mcp_process_present` (`identity_migration.py:374-376`). A conserva correctamente integridad de bytes, pero deja el backup sin receipt.
6. R7 reintenta ese error cuando B ya drenó. El retry encuentra el backup y aborta como `incomplete_runs_backup_artifacts` (`identity_migration.py:354-361`), error que R7 deliberadamente no reintenta.

Impacto: **degradation de liveness persistente**. Un proceso que nunca obtuvo el lock ni escribió autoridad puede convertir una carrera inocua en un gate manualmente irrecuperable. Todos los clientes terminan sin daemon y, por tanto, sin cola.

Un quiet-period o varios scans **antes** de migration no bastan: un cliente pre-R7 puede crear otro loser después del último scan y durante la copia. Reintentar ciegamente `incomplete_runs_backup_artifacts` tampoco es seguro, porque ese nombre puede pertenecer a un intento previo, una colisión legacy o bytes externos.

Corrección requerida `[DESIGN]` — elegir una de estas garantías y especificarla antes de implementar:

1. **Recovery byte-exacto del artefacto propio, recomendada:** antes de publicar el backup, persistir un journal/intent durable con identidad de operación, source path canónico, metadata y hash esperados. En retry bajo ambos locks, solo completar receipt o restaurar/retirar el backup si journal, identidad, source actual y bytes del backup coinciden exactamente. Cualquier diferencia externa sigue fallando cerrada y hace cero writes.
2. **Publicación transaccional equivalente:** escribir a un nombre único de operación, verificar bytes y promover backup+receipt con una máquina recuperable que distinga claramente estado propio de colisión preexistente. Debe documentar crash en cada frontera.
3. **Impedir losers visibles durante toda la copia:** requiere una representación de bootstrap/no-writer verificable antes de que el proceso adopte identidad `--daemon`; el mero lock daemon-side no lo consigue porque el proceso ya aparece en el snapshot antes de intentar el lock.

No es seguro eliminar el segundo quiescence check ni eximir todo daemon que no posea el startup lock: un daemon legacy no cooperativo sí puede ser writer real.

## Gaps adicionales del plan

### MEDIUM-01 — V24 no fija el outcome de crash por frontera

V24 acepta que, tras matar el ganador en cualquier frontera pre/post-migration/bind/activation, una segunda ola “progresa **o falla cerrado**” (`plan:849`). Eso no prueba la recuperación prometida por R7. Debe existir una expectativa exacta por frontera:

- antes de tocar artifacts: el siguiente ganador progresa;
- tras crear artifact propio: recovery byte-exacto progresa o emite un conflicto durable únicamente si hay drift externo demostrado;
- post-bind/pre-activation: el socket del proceso muerto desaparece y el siguiente progresa;
- post-activation: coordinación/recovery de generación se valida y el siguiente progresa.

“Falla cerrado” es correcto ante identidad ilegible, daemon legacy persistente o drift externo; no debe contar como éxito ante la muerte controlada de un fixture cooperativo sin drift.

### MEDIUM-02 — Task 5B ordena modificar `daemon.py` pero no lo incluye en Files

R7 requiere cambiar `run_daemon` (`plan:68,847`) y el propio addendum bloquea modificaciones de `daemon.py` hasta GREEN (`plan:71`). Sin embargo, Task 5B omite `tools/dayz_mcp/daemon.py` de su lista de archivos (`plan:835-841`). Conforme al scope estricto del plan, esto es contradictorio. Debe añadirse explícitamente antes de implementación.

### MEDIUM-03 — salida 0 de loser sin daemon healthy oculta el resultado del launch directo

R7 especifica que lock busy hace probe final y salida 0 aunque el ganador todavía no publique status (`plan:69,847`). Para un cliente MCP el padre seguirá haciendo probes, pero una shell/supervisor directo puede interpretar 0 como daemon arrancado aunque el ganador muera un instante después. El contrato debe distinguir:

- probe final healthy: salida 0 porque otra autoridad quedó acreditada;
- startup en progreso sin health: código/estado retryable y log estable, sin secretos;
- fallo del ganador: el siguiente request/ola reintenta, sin anunciar éxito previo como autoridad confirmada.

## Races R7 correctamente cerradas

- Los hijos lanzados por `_ensure_daemon` pre-R7 cargan el `run_daemon` nuevo desde disco y participan en la misma elección.
- Los launches directos nuevos participan en el mismo gate.
- Dos puertos con el mismo root ya no migran simultáneamente; la autoridad persistente se serializa por root.
- Muerte del owner libera el lock por semántica del SO; la mera existencia del archivo no crea un owner.
- Un candidato que pierde no espera vivo poseyendo capacidad de escritura; sale sin migration/bind.
- Reparse/identity drift/lock I/O e identidad de proceso ilegible permanecen fail-closed.
- Un daemon legacy no cooperativo permanece blocker; R7 no lo confunde con loser seguro.

## V24 mínimo para GREEN

Además de la matriz ya escrita en `plan:849`, el gate debe inyectar un loser exactamente:

1. antes del primer `_assert_quiescent`;
2. después del primer check y antes de escribir backup;
3. después de fsync del backup y antes/durante el segundo check;
4. después del segundo check y antes de publicar receipt;
5. después del receipt y antes de bind/activation.

En todas las variantes cooperativas sin drift debe quedar exactamente una generación healthy y artifacts válidos. Repetir cada frontera con crash del ganador. Las variantes con backup/journal alterado, source cambiado, PID reuse, identidad incompleta o daemon legacy persistente deben fallar cerradas, conservar evidencia durable y hacer cero reparación no autorizada.

El gate mixed-version debe diferenciar un **cliente viejo que lanza un daemon nuevo** —debe progresar— de un **daemon viejo no participante** —puede bloquear de forma diagnóstica—. El gate cross-port debe exigir una autoridad healthy en el puerto ganador y un unavailable explícito en el otro, no dos éxitos ni un deadlock silencioso.

## Condición para GREEN

R7 puede recibir GREEN cuando:

- el backup/receipt tenga recovery durable y byte-exacto para el artifact propio dejado por un retry/crash, o se elimine por diseño la posibilidad de que un loser se vuelva visible durante esa transacción;
- V24 exija progreso en todas las fronteras cooperativas sin drift, no el comodín “progresa o falla cerrado”;
- `daemon.py` figure en el scope de Task 5B;
- el exit contract de un loser directo no declare éxito antes de acreditar health.

## Conclusión

BLOCKED

La elección daemon-side global de R7 es la arquitectura correcta para cerrar R6. Todavía no es implementable con garantías de liveness porque el gate de backup actual no puede recuperarse si un loser aparece entre la creación del backup y el receipt. El retry propuesto convierte `dayz_mcp_process_present` en `incomplete_runs_backup_artifacts` permanente. Hace falta recovery byte-exacto y gates de crash/interleaving antes de tocar producción.
