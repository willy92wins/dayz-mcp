# Revisión adversarial R5 — interbloqueo de arranque

## Veredicto

BLOCKED

Excluir los proxies `--client` es correcto, pero Task 5B no resuelve la elección concurrente del daemon. Con muchos `ClientRuntime` cada proceso posee su propio `_spawn_lock`; pueden crear candidatos `--daemon` simultáneos. Como cada candidato ejecuta identity migration antes del bind, se observan mutuamente como blockers y todos pueden abortar. El plan sigue BLOCKED hasta añadir elección cross-process y re-probe.

## Evidencia contrastada

- El predicado actual identifica cualquier argv que contenga `-m dayz_mcp` (`tools/dayz_mcp/identity_migration.py:127-145`) y después añade el PID a blockers (`:184-225`). Por ello los 52 proxies `--client` explican el deadlock observado.
- La lectura de exe/cmdline incompleta ya falla cerrada como `process_scan_incomplete` (`identity_migration.py:188-215`). R5 la conserva.
- `--client` es el proxy stdio y no construye `RunManifestStore`; daemon/embedded/default sí alcanzan la autoridad de runtime citada por el plan. Excluir únicamente el selector client exacto no amplía capacidad de escritura sobre `runs.json`.
- El backup conserva lock exclusivo, allowed-current identity, dos checks de quiescence, hashes y receipt (`identity_migration.py:302-388`). Task 5B prohíbe tocar esas defensas.

## Matriz argv requerida

| Forma | Clasificación segura |
|---|---|
| `-m dayz_mcp --client` con selector único | no blocker |
| `--daemon` | blocker |
| `--embedded` | blocker |
| sin selector | blocker, porque default embedded |
| dos o más selectores | blocker ambiguo |
| selector desconocido / argv adicional que altere modo | blocker ambiguo |
| `p0s_daemon_bootstrap.py` | blocker |
| exe/cmdline ilegible, vacío o no-string | `process_scan_incomplete` |

La regla debe parsear tokens, no usar substring ni “contiene `--client`”. El client exento debe tener exactamente un selector de modo; flags ordinarios documentados del client pueden existir, pero cualquier token capaz de seleccionar/inyectar otro modo conserva blocker. V23 lo exige expresamente (`plan:835-837`).

## Capacidad real y races

- Un proceso `--client` no posee listener ni abre/migra `runs.json`; su presencia no viola quiescence de autoridad.
- Daemon, embedded, default y bootstrap siguen bloqueando aunque aún no hayan abierto el archivo: la clasificación es por capacidad, no por estado observado.
- Un proceso ambiguo nunca se “supone client”; falla cerrado.
- El lock serializa dos ejecutores cooperativos del gate. Los checks antes y después de leer/copiar detectan aparición de un writer entre snapshots; un segundo daemon que use el arranque canónico también debe atravesar el mismo lock/gate.
- La identidad allowed-current continúa siendo exacta y deriva produce fallo; R5 no autoriza allowlist por PID aislado.
- Ningún gate mata clientes, daemon o procesos DayZ.

### Bloqueante — elección del daemon ocurre después de la migración

- `ClientRuntime._spawn_lock` solo coordina threads dentro de un proceso (`tools/dayz_mcp/server.py:562-577`); 52 proxies tienen 52 locks independientes.
- Cada cliente puede observar cero listener y lanzar su propio candidato.
- `run_daemon` ejecuta `_ensure_identity_migration` antes de intentar el bind (`tools/dayz_mcp/daemon.py:350-378`).
- Tras excluir correctamente los clients, los candidatos se ven entre sí como `--daemon` blockers. El lock/receipt de migration serializa el acceso a artifacts, pero no el derecho a ser el único candidato: el primero que toma el lock también ve los otros candidatos ya creados y falla; los siguientes repiten. Ninguno necesita llegar al bind.

Impacto: **degradation de liveness** persistente; el cambio puede transformar el deadlock “clients bloquean daemon” en “candidatos daemon se bloquean mutuamente”.

Corrección requerida `[DESIGN]`:

1. Adquirir una elección/lock cross-process de arranque antes de spawn o, como máximo, antes de identity migration.
2. El ganador vuelve a probar listener saludable después de adquirir el lock; si ya existe, no migra ni spawnea.
3. Solo el ganador puede aparecer como allowed-current durante migration y llegar al bind.
4. Los perdedores esperan/re-prueban acotadamente el listener/generación; no lanzan otro candidato y no se añaden como allowed identities.
5. Si el ganador muere, el lock del SO se libera y exactamente un waiter reintenta. Un lock-file por mera existencia sin ownership/recuperación no basta.
6. Mantener el bind como autoridad final: si otro proceso externo gana, el candidato elegido sale sin mutar de nuevo.

V24 debe añadir una barrera con N procesos ClientRuntime que observan cero listener simultáneamente. Resultado: un solo candidato atraviesa migration, un solo backup/receipt, una sola generación escucha; los demás conectan a ella. Repetir con muerte del ganador antes/después de migration y con un daemon externo apareciendo entre elección y bind.

## Viability V23/V24

V23 debe cubrir, como mínimo:

- client exacto único positivo;
- daemon, embedded, default, bootstrap, múltiples selectores y unknown negativos;
- exe/cmdline ilegible fail-closed;
- allowed identity match/drift;
- argv con `--client` como valor de otro argumento, substring o junto a otro selector: blocker.

V24 debe mantener dos o más clients reales vivos, cero listener, completar exactamente un `backup-runs-v1` y arrancar una sola generación. La variante negativa introduce daemon/embedded/bootstrap durante cada frontera de los dos checks y exige `dayz_mcp_process_present`, artifacts sin deriva y cero terminaciones.

## Riesgos residuales aceptados

- Un proceso malicioso capaz de falsificar argv client pero escribir `runs.json` está fuera de la frontera cooperativa actual; no se rebaja la validación de exe/identidad ni la integridad hash/receipt.
- Un proceso ilegible puede seguir bloqueando el arranque. Es fail-closed deliberado, no regresión de liveness.
- La evidencia viva final sigue requiriendo cero listener antes del gate y status/doctor limpios después; no se puede fabricar matando los 52 clientes.

## Conclusión

BLOCKED

La clasificación argv propuesta es segura, pero Task 5B no puede implementarse todavía como solución completa: necesita elección cross-process + re-probe antes de migration. El lock/receipt existente protege los datos, no garantiza progreso ni un único candidato daemon.
