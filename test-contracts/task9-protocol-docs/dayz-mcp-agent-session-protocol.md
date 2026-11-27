# Protocolo de sesiones compartidas de DayZ MCP

Este runbook es la fuente L2 para cualquier agente que use DayZ MCP en la caja compartida. Las reglas L1 solo enlazan aquí.

## Modelo de recursos (leer esto primero)

La caja tiene **tres** recursos distintos, no uno. Confundirlos es la causa de la mayoría de los bloqueos entre agentes.

| Recurso | Qué protege | Cómo se obtiene | Estado 2026-07-26 |
|---|---|---|---|
| **Lease** | mutaciones y lifecycle low-level | cola FIFO, TTL 120 s, cap 64 (`session_coordination.py:15-17`) | funciona |
| **Caja** | la instancia de DayZ (el juego): una sola por máquina | cola FIFO de la caja con `dayz_test_run(..., on_busy="queue")`, hasta 600 s (`server.py:784`, `session_coordination.py` `_box_queue`); sin `on_busy="queue"`, rechazo | funciona dentro de cada daemon; las colas de daemons distintos no se ven entre sí |
| **Lecturas** | nada — no esperan | libres, sin lease (`READ_ONLY_COMMANDS`, `session_coordination.py:18-27`) | funciona |

Dos consecuencias que conviene interiorizar:

1. **Tener el lease no te da derecho a lanzar un run.** El lease y la caja son colas distintas. Para lanzar, ponte en la cola de la caja con `on_busy="queue"`; sin ella, `active_run_exists` es la respuesta esperada con la caja ocupada, no una avería.
2. **Compartir el juego ya funciona; lo que no se comparte es el lanzamiento.** El dispatch de comandos se autoriza por el lease, no por quién lanzó el run (`loopback.py:326-333`). Si ya hay un juego vivo con los mods que necesitas, **no lances otro**: adquiere lease y trabaja sobre el que hay.

Mejora planificada (run compartido con stack unión, cola para el recurso run, zonas por agente): `DayZ_MCP_dev/plans/2026-07-26-multi-agent-run-sharing-plan.md`.

## La caja está ocupada: ponte en la cola (regla de bolsillo)

1. La caja de DayZ es una por máquina. Mira `session_status()` → `box.occupied` (y `box.foreign_holder`, que nombra la instancia, el titular y el propósito del otro). Un daemon o un arnés que solo escucha en otro puerto (por ejemplo el 8797) **no** ocupa la caja: sin un DayZ vivo, la caja está libre.
2. Si está ocupada, llama a `dayz_test_run(..., on_busy="queue")` y espera tu turno. No sondees buscando un hueco: los relevos por la cola no dejan hueco libre.
3. Nunca mates un DayZ, nunca cierres un arnés, una sesión o un daemon ajeno y nunca borres `C:\tmp\caja-dayz\m1.espera`. Tampoco preguntes a Guillermo si cerrar lo de otro: esperar en la cola es la respuesta.
4. Si te para `run_protected` o `m1.espera`, espera en la cola. Un run abandonado lo cierra el vigilante (`idle_warden`), que viene encendido por defecto.
5. Si la espera vence (600 s), vuelve a ponerte en la cola. En scripts, `active_run_exists`, `box_reserved` o una espera vencida son contención: no son un fallo del test ni cuentan como intento fallido.
6. El lease es solo para mutar en el juego: suéltalo al acabar y no lo retengas sin partida.

Límites conocidos (2026-11-27, consultoría de la cola): la cola vive en la memoria de cada daemon, así que LIVE, experimental y los daemons de otros arneses no ven las colas de los demás; y lo que se lanza fuera del MCP (DayZDiag sueltos, bancos de otros proyectos) no puede pedir turno. Con un DayZ ajeno vivo, `on_busy="queue"` espera igual a que salga, pero sin orden respecto a la cola del otro daemon.

## `credential_source_untrusted` / `daemon_identity_unverified`: editar el MCP desarma a los demás

Diagnosticado 2026-07-26 (BUG-062). **Perder las tools a mitad de sesión es lo normal en esta caja, no una avería tuya.**

Mecanismo: el cliente acredita al construirse una tupla de autoridad que incluye `security_build_id`, `authority_sha256`, `argv`, `native_executable`, `cwd` y keyfile. Si esa tupla cambia —o `policy.revalidate()` falla— toda llamada muere con `credential_source_untrusted` (`daemon_credential.py:57-100`); el transporte da `daemon_identity_unverified` (`accredited_daemon_transport.py:273`). Es el guard haciendo su trabajo.

**Disparador dominante: el churn del daemon.** Con `--idle-timeout 600` el daemon se autoapaga a los 10 minutos de inactividad y el siguiente cliente lo re-spawnea, con `daemon_generation` nueva. Cada reemplazo desarma a todos los clientes que acreditaron la generación anterior. Observado en directo: daemon PID 49216 creado 19:32:23, sustituido por PID 52524 a las 20:14:16. No hace falta que nadie toque nada: basta con que la caja esté ociosa 10 minutos.

**Disparador adicional: editar `DayZ_MCP_dev/tools/dayz_mcp/*.py`** cambia la misma tupla y produce el mismo efecto sobre toda la caja.

Evidencia del caso: dos sesiones distintas (Claude y Codex) perdieron las tools el mismo día; keyfile **sin** rotar (mtime del día anterior), así que la rotación de clave queda descartada. El incidente `unauthorized` de LFPowerGrid del 2026-07-25 es la misma clase.

**Recuperación: abrir una sesión nueva.** Las tools cargan sólo al arranque del proceso, así que una sesión viva no se recupera. NO mates el daemon, NO re-registres el MCP, NO rotes el keyfile y NO levantes un cliente local como bypass: ninguna de esas cosas ataca la causa y todas pueden tumbar trabajo ajeno.

**Diagnóstico correcto antes de concluir:**

- `doctor --daemon-policy normal --json` con `CONFIG_UNREADABLE` **no** significa que el registro esté roto. El doctor no lee los ficheros: lanza los CLI `claude`/`codex mcp get` y trata `returncode != 0` como ilegible (`doctor.py:307-309`). Verifica el registro leyendo `~/.claude.json` y `~/.codex/config.toml` a mano antes de tocar nada.
- Comprueba el listener y la topología host-direct: `Get-NetTCPConnection -LocalPort 8765 -State Listen` y `Get-CimInstance Win32_Process` filtrando `dayz_mcp`. Sano = exactamente un `--daemon`; todo lo demás `--client`.

**Prevención (regla de convivencia):** tocar el código del propio MCP es una operación con efecto sobre toda la caja, no un cambio local. Anúncialo, o hazlo cuando no haya otras sesiones dependiendo de las tools. Editar `dayz_mcp/*.py` mientras otro agente corre un gate le produce un RED que no es suyo.

## Clasificación de operaciones

| Clase | Ejemplos | Lease |
|---|---|---|
| Lecturas puras | `session_status`, `bridge_status`, telemetría sin side effects, doctor y observación de estado | No esperan lease. |
| Mutaciones | spawn, cámara, tiempo, clima, controles y cualquier tool desconocida | Requieren lease activo. |
| Lifecycle de test aprobado | start, extend o stop de un run gestionado | Usar `dayz_test_run`/`dayz_test_stop`; la tool gestiona cola, lease, heartbeat y liberación. Stop/extend requieren `run_id` exacto. |
| Lifecycle low-level | adopt/reap u otra operación no cubierta por las tools de test | Requiere lease activo y `run_id` exacto. |

## Matriz de ejecutables y lifecycle

| Ruta | Ejecutable / rol | Contrato |
|---|---|---|
| Diag gestionado | `DayZDiag_x64.exe`, incluido el rol `-server` | `managed_lifecycle=true`; start mediante `dayz_test_run`; stop/extend mediante el `run_id` exacto. |
| Servidor dedicado | `DayZServer_x64.exe` | `managed_lifecycle=false`; probe-gated y no lo inicia el launcher oficial. |
| Retail manual externo | Sesión abierta por el usuario fuera del launcher | Sin lifecycle de agente; activa cuarentena. |

## Protocolo normal

1. Ejecuta `session_status`. Si existe presencia retail externa, aplica la cuarentena retail descrita abajo.
2. Para arrancar o extender un proyecto aprobado, usa `dayz_test_run`; para detenerlo, usa `dayz_test_stop(run_id)`. No preadquieras un lease: estas tools exigen una sesión propia inicialmente ociosa y encapsulan `session_acquire_wait`, heartbeat, liberación y verificación terminal.
3. Para cualquier mutación o lifecycle low-level no cubierto por esas tools, llama `session_acquire_wait(purpose, max_wait_s)` antes de la primera acción. La llamada permanece en FIFO y solo retorna cuando obtiene lease activo; nunca retorna `queued`.
4. `session_acquire`/`session_wait` son primitivas low-level de compatibilidad. Si las usas y abandonas una espera, llama `session_cancel(ticket)`. El high-level cancela por operation id incluso si la request HTTP inicial termina tarde.
5. La cola es FIFO. Ticket y lease expiran exactamente a `120 s`; a los 120 s ya no autorizan. Usa `session_heartbeat(lease_token)` solo mientras haya trabajo exclusivo activo, no para reservar la caja durante lectura, razonamiento o espera humana.
6. Para lifecycle, solo el launcher nativo aprobado puede pasar identidad y lease mediante `DAYZ_MCP_CLIENT_ID_JSON` y `DAYZ_MCP_LEASE_TOKEN` en el entorno privado del hijo. El registro canónico contiene `dayz-test-v1`, fijado por root, identidad de archivo, ruta relativa y SHA-256. Nunca copies esos valores a una shell, argv, logs, HANDOFF ni documentación durable.
7. `run_id` es la única identidad válida para stop, adopt o extensión. El mismo mod no concede ownership y ni el nombre, cmdline, perfil o parentesco sustituyen al `run_id` registrado. Adoptar, extender o reemplazar siempre revalida ese run exacto.
8. Al terminar una secuencia low-level exclusiva, llama inmediatamente `session_release(lease_token)` y después `session_status`; no mantengas el lease durante análisis posterior. Tras `dayz_test_run`/`dayz_test_stop`, inspecciona `session_status`: la propia tool ya debe haber liberado y verificado su lease.

Si una mutación tarda lo suficiente para necesitar renovación, el heartbeat forma parte de esa misma secuencia exclusiva. Un fallo de heartbeat o release no autoriza un kill, una adopción alternativa ni un segundo acquire a ciegas.

La espera larga es request-bound: depende de que sigan vivos el host y la llamada MCP. No es un job durable, no sobrevive restart/desconexión y no reanuda razonamiento. Un timeout/cancel instala un tombstone acotado que impide grants tardíos.

### Launcher nativo de `dayz-test`: H9 resuelto

Existe una ruta agent-safe productiva: las tools `dayz_test_run` y `dayz_test_stop` validan `dayz-test-v1` contra `approved-launchers.json`, abren y verifican su bundle, y ejecutan la transacción del launcher nativo. La transacción adquiere el lease en FIFO, mantiene el heartbeat, redacta identidad/token de ambos streams y libera + verifica el estado terminal incluso durante cleanup.

No ejecutes directamente el comando histórico de `dayz-test.ps1`, no invoques el launcher nativo fuera de la tool y no copies credenciales a una shell para reproducir su contrato. Si el launcher falla por drift de root, identidad, ruta o SHA, se reconstruye y registra mediante el procedimiento autoritativo del proyecto; nunca se relaja la validación.

Evidencia verificada el 2026-07-24:

- Tools públicas y contrato recomendado: `DayZ_MCP_dev/tools/dayz_mcp/server.py:802-807`, `DayZ_MCP_dev/tools/dayz_mcp/server.py:915-995`.
- Apertura del launcher aprobado y ejecución del request: `DayZ_MCP_dev/tools/dayz_mcp/dayz_test_tool.py:477-516`, `DayZ_MCP_dev/tools/dayz_mcp/dayz_test_tool.py:528-553`.
- Adquisición, heartbeat y cleanup transaccional: `DayZ_MCP_dev/tools/dayz_mcp/native_launcher_transaction.py:115-151`, `DayZ_MCP_dev/tools/dayz_mcp/native_launcher_transaction.py:176-207`.
- Registro nativo fijado: `DayZ_MCP_dev/tools/approved-launchers.json:4-13`.

## Retail externo y cuarentena

Retail solo puede aparecer como sesión manual externa. Mientras esté presente, se permiten lecturas puras, pero quedan bloqueadas las mutaciones y todo lifecycle. La sesión o usuario que abrió retail debe cerrarlo mediante la UI, ejecutar doctor/rescan y registrar el resultado.

Si el agente pierde acceso a la UI, declara `manual_cleanup_required`. Ningún otro agente mata, adopta ni atribuye ownership al proceso para desbloquear la caja.

## Administración, diagnóstico y cierre degradado

- `python -m dayz_mcp.admin_cli` es una ruta administrativa excepcional: exige TTY interactiva real, motivo no vacío y confirmación exacta. No es una tool normal ni un bypass de la cola.
- Desde `tools`, `.\.venv-mcp\Scripts\python.exe -m dayz_mcp.doctor --json` produce un diagnóstico read-only. `session_status` es el gate autoritativo de sesión/cola; doctor añade topología, lifecycle y presencia retail.
- Ante release fallido, estado ambiguo o run no reconciliado, conserva procesos ajenos, marca el cierre como degradado y registra `UNRECONCILED` o `manual_cleanup_required` según corresponda. No sustituyas la evidencia por scans o kills.
- Riesgo residual: otro proceso bajo la misma cuenta del sistema puede inspeccionar el entorno de una shell del mismo usuario. Reduce el riesgo manteniendo las dos variables de lifecycle solo durante la llamada y limpiándolas después; nunca copies sus valores a un artefacto durable.

## Checklist obligatorio de cierre y handoff

1. `session_release` si existe lease propio.
2. `session_status`: `own_lease=none`, `own_ticket=none`, `pending_commands=0`.
3. Confirmar `vehicle_control=inactive` o deadman observado.
4. Declarar el run como `RUNNING_IDLE`, `EXITED` o `UNRECONCILED`.
5. Solo escribir HANDOFF si hubo cambio durable, incidente o cleanup degradado.

Comprobación separada de presencia retail: confirmar ausente tras el cierre por UI o registrar `manual_cleanup_required`; esta comprobación no cambia ni omite los cinco puntos anteriores.

## Runs muertos: auto-heal y reap seguro (added 2026-07-16, D-18/BUG-043/LL-195)

Un crash de DayZ deja un run `RUNNING_IDLE` con PIDs muertos que antes bloqueaba la caja permanentemente (`start`→`active_run_exists`, `adopt`→`process_identity_mismatch`, `stop`→`run_not_adopted`; ni el restart lo limpiaba). Desde 2026-07-16 el daemon lo maneja solo:

- **Auto-heal**: el daemon repa a `EXITED` cualquier run cuyos PIDs estén TODOS confirmados-muertos (tick de 30 s). No hace falta intervención — tras un crash, la caja se desbloquea sola en ≤30 s. Requiere que el daemon corra el código con el reaper (un daemon fresco lo tiene; si el daemon lleva vivo desde antes del fix, reiniciarlo — matarlo y dejar que la siguiente llamada lo re-spawnee — lo activa y limpia el ghost en el primer tick).
- **Reap explícito (agent-callable, sin TTY)**: si no quieres esperar el tick, `python -m dayz_mcp.lifecycle_cli reap --run-id <id>` (con lease activo, identidad+token por env como stop/adopt). Solo triunfa si el run está all-dead; un run con procesos vivos o diag ambiguo devuelve `run_not_reapable` — ese caso peligroso sigue exigiendo el force-clear TTY (`admin_cli`).
- **`admin_reconcile` — matiz `--empty` vs `--pid`**: `--empty` exige que el run NO tenga process records (`invalid_reconcile_request` si los tiene). Un ghost `RUNNING_IDLE` conserva sus records → usar `--pid <pid_muerto>`, no `--empty`. (Con el reaper esto casi nunca hace falta.)

Regla que no cambia: nadie mata procesos por nombre/mod/PID-aislado; el reap solo actúa sobre runs sin ningún proceso vivo (fail-closed ante mismatch de PID reusado, guard caído o cuarentena retail).

## Ciclo agente rebuild-relaunch y timing del lease (added 2026-07-16, LFHeli · LL-197/198)

Aprendido operando `dayz-test.ps1` + lifecycle MCP en un ciclo de test iterativo:

- **El lease de 120 s cuenta wall-clock, incluido el razonamiento del agente entre tool calls.** Preparar el comando (rutas, args, token placeholder) ANTES de `session_acquire`; `acquire` y el primer uso del token en turnos adyacentes, sin análisis largo entre medias. Las operaciones bloqueantes (build+launch) en FOREGROUND consumen el lease en trabajo real (preferible a background+heartbeat manual, cuyo timing es impredecible). Si hay que razonar mucho, re-adquirir justo antes de usar. No asumir que "acabo de adquirir" sigue vivo tras varios turnos de análisis. (LL-197)
- **`session_release` desasocia el run del lease.** Para pararlo en el siguiente ciclo hay que `adopt` primero — y `adopt` da `process_identity_mismatch` si un peer del run (p.ej. el client) murió entre medias. El momento SEGURO para `adopt`→`stop` es con server+client AMBOS vivos. Al iterar in-game: pedir al usuario NO cerrar el juego entre ciclos (dejar ambos abiertos + avisar); el agente hace `adopt`→`stop`→`build`→`launch`. Si el client ya murió: el usuario cierra el server por UI (run all-dead → auto-heal ≤30 s) o `admin_reconcile --pid <pid_muerto>` (TTY). El `reap` agent-callable NO sirve con el server vivo (`run_not_reapable`). (LL-198)
