# Revisión adversarial R6 — elección cross-process de arranque

## Veredicto

BLOCKED

R6 serializa correctamente a los `ClientRuntime` **nuevos que usan el mismo puerto**, recupera el lock cuando muere su propietario y conserva el bind como autoridad final. No obstante, la exclusión se instala únicamente antes del `spawn` en `ClientRuntime`; no cubre clientes ya cargados con el código anterior ni arranques directos de `--daemon`. Esos procesos pueden crear un segundo candidato después del último scan y antes de migration/bind. Como migration ocurre antes del bind, dos candidatos pueden seguir observándose como blockers y morir ambos. Además, el lock se separa por puerto aunque `runs.json` y `coordination.json` son recursos compartidos por `RuntimePaths.root` y no por puerto.

## Evidencia contrastada

- El plan limita el mecanismo nuevo al lado cliente antes de spawn (`plans/2026-07-22-bug046-lease-queue-liveness-plan.md:65-66`) y Task 5B solo modifica `identity_migration.py` y `server.py`, no `daemon.py` (`:829-837`).
- Cada cliente cargado actualmente conserva un `_spawn_lock` exclusivamente intra-proceso y lanza tras dos probes (`tools/dayz_mcp/server.py:559-580`). La ruta se ejecuta de nuevo ante cada fallo de conexión (`server.py:623-641`), por lo que los 52 clientes vivos no adoptan mágicamente el lock R6 al cambiar los archivos en disco.
- Un hijo lanzado usa `python -m dayz_mcp --daemon --port ...` (`tools/dayz_mcp/daemon.py:431-449`). Un arranque directo usa la misma entrada `run_daemon` (`tools/dayz_mcp/server.py:1289-1294`) y el plan no le exige participar en la elección cliente.
- `run_daemon` prueba health y después ejecuta identity migration **antes** de construir estado y bind (`tools/dayz_mcp/daemon.py:350-382`). Por ello el bind no puede resolver una carrera en la que ambos candidatos abortan durante migration.
- El scanner actual enumera todo proceso Python dirigido a `dayz_mcp`, sin filtrar por puerto (`tools/dayz_mcp/identity_migration.py:164-233`). La clasificación R6 seguirá siendo global para todo writer-capable porque esos procesos comparten autoridad local.
- `RuntimePaths.from_env` fija un único root `%LOCALAPPDATA%/DayZ_MCP`, con `coordination.json` y `runs.json` comunes (`tools/dayz_mcp/runtime_state.py:82-96`). La activación del daemon abre esos mismos stores, sin incorporar el puerto al path (`tools/dayz_mcp/daemon.py:147-189`).
- La migration permite únicamente la identidad exacta del proceso actual y rechaza cualquier blocker adicional (`tools/dayz_mcp/identity_migration.py:325-352`). El lock de artifacts protege los bytes, pero no convierte a dos candidatos simultáneos en un ganador.

## Hallazgos bloqueantes

### HIGH-01 — participantes nuevos solamente; los 52 clientes vivos eluden la elección

Task 5B coloca el lock en `ClientRuntime._ensure_daemon` (`plan:843`). Un proceso Python ya vivo mantiene en memoria la implementación anterior; editar `server.py` no sustituye su método. Ante el siguiente fallo de conexión, cualquiera de esos clientes puede ejecutar el spawn actual (`server.py:623-641`) mientras un cliente R6 posee su lock. El candidato fuera de elección aparece en la ventana scan → spawn/migration y ambos daemons se bloquean en el gate pre-bind.

Impacto: **degradation de liveness**. El rollout vivo pedido en Step 5 (`plan:847`) no está demostrado y puede repetir el deadlock sin terminar ningún cliente.

Corrección requerida `[DESIGN]`:

1. Añadir una estrategia explícita de compatibilidad mixta. La opción robusta es que todo candidato nuevo participe también en un gate de arranque antes de migration, independientemente de quién lo haya lanzado.
2. Los candidatos que no ganen no deben permanecer como procesos writer-capable mientras el ganador hace su scan: deben salir de forma no bloqueante o adoptar una identidad de bootstrap demostrablemente no-writer; el ganador espera su desaparición antes de migration.
3. Mantener el gate hasta que el ganador haya completado migration, bind y activación. Si muere, el ownership del SO debe liberarse automáticamente.
4. Si se elige una estrategia operacional en vez de daemon-side, Step 5 debe definir una quiescencia reproducible sin matar clientes: detener temporalmente nuevas tool calls, arrancar y verificar un único daemon canónico y solo entonces reabrir tráfico. “Dejar que el supervisor normal arranque” no cierra la carrera.

V24 debe incluir procesos que ejecuten deliberadamente el `_ensure_daemon` pre-R6 mientras otros usan R6. El criterio no es solo “un `spawn_fn`”: exactamente un candidato puede entrar en migration y debe quedar un listener saludable; los demás salen o conectan sin bloquearlo.

### HIGH-02 — un daemon directo/external puede aparecer dentro de la ventana protegida solo por el cliente

R6 admite que un daemon externo entre elección y bind cause “salida segura/fallo del gate” (`plan:66`) y V24 solo exige “cero segunda autoridad” (`plan:845`). Eso prueba safety, pero permite el resultado de cero autoridades: el externo y el elegido se ven mutuamente antes del bind y ambos fallan `dayz_mcp_process_present`.

Impacto: **degradation de liveness**. Se preserva integridad, pero las ejecuciones vuelven a quedar sin daemon y no pueden entrar en cola, que es precisamente el objetivo de BUG-046.

Corrección requerida `[DESIGN]`: mover o extender la elección al entrypoint writer-capable (`run_daemon` y cualquier bootstrap aprobado) para que el protocolo no dependa de que el launcher sea un `ClientRuntime` nuevo. El gate de integración debe exigir **exactamente una autoridad saludable**, no solo “no dos”, cuando el externo también implementa R6. Un ejecutable antiguo/no cooperativo puede seguir provocando fail-closed, pero debe diagnosticarse explícitamente como incompatibilidad de versión y no contarse como recuperación exitosa.

### HIGH-03 — lock por puerto para autoridad persistente global

Step 3 separa el lock por puerto (`plan:843`), mientras todos los puertos de un mismo usuario apuntan al mismo `RuntimePaths.root`, `runs.json` y `coordination.json` (`runtime_state.py:82-96`; `daemon.py:147-189`). Dos clientes R6 en puertos distintos adquieren locks distintos, hacen spawn simultáneo y sus candidatos se bloquean entre sí porque el scanner y el gate de migration son globales, no port-scoped (`identity_migration.py:164-233,302-322`).

Impacto: **degradation de liveness** y diseño de exclusión inconsistente con el recurso protegido. No requiere clientes antiguos para reproducirse.

Corrección requerida `[DESIGN]`: la elección que protege migration/autoridad persistente debe estar keyed por la identidad canónica de `RuntimePaths.root` —o por cada recurso mutable real—, no solo por puerto. Si se pretende soportar varias autoridades por puerto, antes hay que separar también `runs.json`, `coordination.json`, audit/fault y lifecycle por namespace; eso sería un cambio de arquitectura fuera de Task 5B.

V24 debe lanzar simultáneamente dos configuraciones con puertos distintos y el mismo root. Bajo el diseño actual esperado, una sola generación puede poseer la autoridad global y la otra debe fallar de forma diagnóstica sin bloquear a la ganadora.

## Races y recuperación que R6 sí resuelve

- Entre clientes R6 del mismo puerto y root, ownership por handle + re-probe bajo lock evita que todos hagan spawn (`plan:65,843`).
- La muerte del **cliente** propietario libera un lock del SO. Si su child sobrevive y se vuelve healthy, el siguiente waiter puede descubrirlo (`plan:66,845`).
- Un lock-file residual sin owner no debe bloquear; la propiedad no se infiere por existencia (`plan:65,843`).
- Un candidato ilegible o persistentemente unhealthy falla cerrado, sin kill ni lanzamiento rival (`plan:66`). Ese outcome es seguro, aunque debe quedar diagnosticado como unavailable y no como éxito.
- El bind sigue siendo la última prueba de autoridad frente a una carrera que alcance esa fase (`daemon.py:371-382`). No repara carreras que abortan antes.

## Gates V24 adicionales requeridos

1. **Mixed-version:** N clientes R6 + al menos dos wrappers con la conducta actual/pre-R6; todos observan cero listener. Debe sobrevivir exactamente una autoridad saludable.
2. **Direct daemon:** inyectar `python -m dayz_mcp --daemon` después del re-probe cliente y antes de spawn, después de spawn y durante migration. Para participantes R6, siempre queda exactamente un listener saludable.
3. **Cross-port/shared-root:** dos puertos distintos con el mismo `%LOCALAPPDATA%/DayZ_MCP`; nunca deben entrar dos candidatos a migration.
4. **Owner death:** muerte del propietario antes de spawn, post-spawn/pre-migration, durante migration y post-bind. El siguiente intento progresa o devuelve un fault diagnóstico; nunca lanza un rival mientras el candidato writer-capable siga vivo.
5. **Lock residual/fresh root:** archivo residual desbloqueado no bloquea; handle poseído sí espera; muerte del owner libera; root inexistente se inicializa con permisos previstos o falla con error estable sin spawn.
6. **PID/identidad:** candidate scan revalida identidad completa en cada espera; desaparición y PID reuse no se interpretan como el mismo candidato.
7. **Fail-closed:** cmdline/exe ilegible, embedded, bootstrap o proceso legacy no cooperativo producen error público estable, cero kill y cero segundo spawn. El gate distingue este resultado de una recuperación liveness exitosa.
8. **Presupuesto temporal:** el timeout de elección + candidate wait debe trazarse al timeout de `_call`; un arranque legítimo que sigue progresando no debe convertirse prematuramente en `daemon_unavailable` (`server.py:623-641`).

## Condición para GREEN

R6 puede pasar a GREEN cuando el plan:

- proteja el recurso global correcto, no únicamente un puerto;
- cierre el bypass de clientes ya cargados y launchers directos con una estrategia implementable y probada;
- exija progreso a exactamente una autoridad saludable en las races cooperativas, además de ausencia de doble autoridad;
- preserve fail-closed para procesos antiguos/ilegibles sin presentar ese bloqueo como liveness resuelta.

## Conclusión

BLOCKED

La elección client-side es necesaria, pero no suficiente. R6 todavía permite candidatos fuera de la elección y crea locks distintos para writers que comparten el mismo estado persistente. En ambos casos la carrera ocurre antes del bind, de modo que “bind como autoridad final” no garantiza que quede autoridad alguna. No se debe modificar producción hasta incorporar compatibilidad mixta, participación del daemon/gate pre-migration y exclusión keyed por `RuntimePaths.root`, con los gates anteriores.
