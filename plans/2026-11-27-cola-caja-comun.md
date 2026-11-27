# Plan: una cola de la caja para todos (fases 2 y 3), v2

Pedido por Guillermo el 27/11 («Todo lo P0, por fases»), sobre la consultoría de Neon (solo lectura, main 1e105d1; informe en el PC, `C:\temp\neon-cola-1127\INFORME-neon.md`, sha256 ac35ed65…059374). La fase 1 (docs) es #466. Cada fase es una PR aparte, Python y docs, sin PBO ni Enforce.

v2 incorpora la revisión R22 de Sol sobre la v1 (084e6a3; veredicto CHANGES, 6 P1 y 3 P2, sin P0; `C:\temp\neon-cola-1127\REVIEW-plan-sol.md`, sha256 47505b1f…5fd510). Cada hallazgo es ahora una regla de diseño o un criterio, marcado [S1]…[S9]. Sin segunda ronda: la revisión de código de cada PR la hace Sol.

Problema que traza cada cambio:

- P-a: la cola de la caja vive en la RAM de cada daemon (`session_coordination.py:420`, `box_wait_touch` :2559). LIVE, experimental, xai y los daemons de los arneses de Grok Bot (8797, 8801) no ven las colas de los demás.
- P-b: lo que se lanza fuera del MCP no puede pedir turno: el cliente B del #432, LFPowerGrid y el banco dsbx. Solo existe `m1.espera`, que es binaria (`box_reservation.py`).
- P-c: un lease sin run se renueva siempre con solo latir (`daemon.py:170-171` → `session_coordination.py:1473`). Es la ficha 37a3.

Fuera de alcance: Workbench, el exit 75 por cwd, tocar LFPG, dsbx o Grok Bot (lo hace Guillermo con el texto que le demos) y quitar `m1.espera`, que se queda tal cual.

## Fase 2: el lease sin run también caduca (P-c), v3

Historia. En la v2 la señal del launcher era un registro interno ligado al `lease_id`. NC3 (Grok, 27/11, con las citas comprobadas) mostró que esa señal no existe sin un endpoint nuevo:
- la transacción y la espera del build lock corren en el proceso cliente (`secure_launcher.py:161`, `dayz_test_worker.py:1812-1827`);
- el latido solo manda `lease_token` (`control_client.py:911-917`);
- `SESSION_ROUTES` (`loopback.py:375-384`) no tiene ningún verbo de transacción.

Guillermo eligió el 27/11 15:22:50Z «Mover la espera al daemon».

Diseño. El run existe en el daemon antes de que empiece la espera:

1. `dayz_test_run` (y el CLI del launcher) pide al daemon, ya con el lease y antes del build lock, que cree la fila del run en `STARTING`, con dueño, lease y `run_id`. Es la misma admisión que hoy hace `start_run`: la caja, `m1.espera` y, con 3A, el ticket de la cola. Después pasa al build y por fin al spawn sobre esa misma fila.
   - El implementador elige si eso es partir `start_run` en «reservar» y «lanzar», o adelantar la fila que ya crea, y lo justifica.
   - No hay endpoint para «marcar mi lease como vivo». La fila es un run de verdad: ocupa la caja, se ve en `session_status` y la paran las mismas vías.
2. Una fila en `STARTING` tiene un plazo duro: la espera del build lock, más el build, más el arranque del servidor, del payload ya acreditado, con un tope fijo. Al vencer, el daemon la pasa a `EXITED` con `reason="starting_timeout"`, sin spawn si aún no lo hubo.
3. `idle_hold_probe`: sin ningún run propio en `RUNNING`, `STARTING`, `STOPPING` ni `UNRECONCILED`, el lease sin run hace hold y caduca a los 1800 s sin trabajo. Con una fila propia en `STARTING` no hace hold, pero esa fila ya tiene su plazo.

Efecto de rebote buscado: la caja queda reservada desde que empieza el build. Desaparece el `box_taken_during_build` (`box_occupancy.py:782-823`), y la cola común de 3A pasa su claim a esa fila.

Criterios:
- F2-pos [S2]: un lease sin run que solo late cada 40 s recibe `renewal_refused="work_idle"` en t=1800, con el vencimiento del último latido válido; el siguiente con ticket vivo obtiene el lease.
- F2-neg1 [S2]: una operación autorizada mueve `last_work_at` y el latido extiende el lease.
- F2-neg2 [S1]: un `dayz_test_run` por la ruta real con el build lock retenido 1900 s (con un presupuesto mayor que el default) tiene su fila en `STARTING` todo el rato y su lease no caduca. `session_status` muestra la caja ocupada por esa fila.
- F2-neg2b [S1]: un lease público que solo late, con `purpose="dayz-test"` y sin fila, sí caduca.
- F2-timeout: una fila en `STARTING` que pasa su plazo queda en `EXITED`, con `starting_timeout`, cero spawns y la caja libre. Si el build acaba después, no lanza.
- F2-cancel: cancelar la llamada durante el build deja la fila en `EXITED` sin spawn (los caminos de cancelación de `STARTING` existen: `process_lifecycle.py:2763`).
- F2-neg3 [S2]: `STARTING`, `STOPPING` o `UNRECONCILED` de otro lease no protegen.
- F2-race [S2]: una operación registrada con el lock del probe soltado hace que el latido no rehúse.
- F2-reg: #391 (entrada humana), la suite de `start_run` y la de cancelación de `STARTING` siguen en verde. Se actualiza `test_lease_work_idle.py:718`.

Va después de 3A, porque las dos tocan la admisión de `start_run`.

### R22 de Sol sobre la v3 (NC5, 27/11; CHANGES, 5 P1 y 1 P2, sin P0; `C:\temp\cola-f2\REVIEW-f2v3-sol.md`, sha256 e5169c3d…1d68ad3). Reglas y criterios añadidos, sin segunda ronda:

- [T1] Ruta: `start_run` se parte por dentro y se reutiliza `POST /lifecycle/start`, sin endpoint nuevo.
  - La admisión persiste el `STARTING` con dueño, lease y `run_id`, responde en cuanto lo persiste y suelta `box_admission()` y los demás locks.
  - El daemon espera el build lock y continúa con el broker sellado y el payload acreditado.
  - La continuación se liga a sesión, lease, generación, `run_id`, `launch_operation_id` y hash. Un reintento exacto observa el mismo intento y conserva su plazo; el launcher lo sigue por `/lifecycle/status` y confirma por `/lifecycle/ack`.
  - El start directo conserva su contrato.
  - Criterio: reintentar el mismo intento no da `launch_identity_conflict` ni `run_not_extensible` (`process_lifecycle.py:5311`, :5372).
- [T2] Plazo: un vencimiento monotónico que se fija en el daemon al admitir. Es la espera acreditada del lock, más el build, más la preparación y el spawn, con un tope numérico de política, y F2-neg2 tiene que caber en él.
  - Un temporizador del daemon liquida la reserva aunque el caller desaparezca.
  - El último guard antes de `Popen` comprueba plazo, autoridad y cancelación.
  - Antes de `Popen`, `starting_timeout` identifica el intento y acredita cero spawns. Después, se cierra por identidad y queda `UNRECONCILED` si hay un proceso sin confirmar.
  - La espera UDP consume el remanente en `RUNNING` (`dayz_test_readiness.py:64`).
- [T3] Cancelación: `_LaunchCancel` se registra antes de publicar la fila. El daemon liquida de forma idempotente el intento exacto ante el stop del titular, la cancelación MCP o CLI, la pérdida del lease y un fallo del build.
  - Bloquea la continuación antes de soltar el claim.
  - El build lock se mantiene hasta que acaba el builder.
  - En la ventana `RUNNING` sin ack, el stop gana al ack.
  - F2-cancel fuerza tres casos: la espera del lock, el build en curso y la carrera entre `Popen` y el ack (`process_lifecycle.py:5555`, :7421, :6382).
- [T4] Persistir una fase que pruebe que el spawn aún era imposible.
  - `admin_reconcile --empty` respeta una reserva viva o la cancela por la misma vía.
  - Tras un reinicio, se retira la reserva demostrada y se conserva la recuperación por intent e identidad donde pudo haber spawn.
  - Criterio: un reinicio a cada lado de esa frontera.
  - El vigilante y el watchdog no liquidan `STARTING`: el plazo es solo de T2 (`process_lifecycle.py:11217`, :10088).
- [T5] Con 3A, bajo `box_admission()` el claim exacto se liga a la fila, también en el start inmediato y en el CLI con la FIFO vacía.
  - Antes del spawn se revalidan el claim, `m1.espera` y las sondas de proceso y puerto.
  - Si se pierde el claim, no continúa aunque el build acabe.
  - `takeover` sigue eligiendo solo `RUNNING` o `RUNNING_IDLE`, y mira la cola antes de desalojar.
  - Criterios: la ocupación vista desde otro daemon, la pérdida del claim y el avance del siguiente ticket.
- [T6] Ajustes de criterios:
  - F2-pos usa el probe real y comprueba los dos bordes: rehúsa en t=1800 y el siguiente obtiene el lease en t=1880 con latidos cada 40 s.
  - F2-neg2 observa desde otro cliente (el `session_status` del caller espera su `tool_lock`) y exige un solo lanzamiento con el mismo ID al soltar el build lock.
  - F2-reg cubre el CLI sellado, `preflight` y las extensiones `client` y `offline` con `run_id`, que preservan el proceso previo si falla su preparación. Se mantienen los negativos de #391 (entrada humana, señal incierta, binding ausente).

## Fase 3: una sola cola para la caja física (P-a, P-b)

### 3A. La FIFO compartida es la única autoridad [S3]

Un documento, `box-queue.json`, en `server_cli.shared_root()` (`%LOCALAPPDATA%\DayZ_MCP_shared`).
- Se muta solo bajo `box-queue.lock`, abierto con `server_cli.open_lock_file` (el abridor por plataforma: en Windows impide borrar el lock abierto). `byte_lock.open_lock_file` no vale aquí, porque usa `O_CLOEXEC` y `O_NOFOLLOW`. [S5]
- La espera al lock es acotada.
- Se escribe con `runtime_state.atomic_write_json`, y un fallo de `os.replace` conserva el documento anterior. [S5]

Módulo nuevo `tools/dayz_mcp/shared_box_queue.py`, con funciones puras sobre el documento y una capa fina de E/S.

`_box_queue` deja de ser una cola propia. Altas, refrescos, claims, cancelaciones y purgas van todas al documento compartido bajo el mismo lock, y la RAM del daemon solo guarda el `ticket` propio de cada espera. No hay dos FIFO que puedan discrepar.

Cada entrada tiene:
- `ticket`;
- `kind` (`mcp` o `external`);
- `instance`;
- `holder` (el session id público, o en un externo el `--holder` dado, por defecto `external`);
- `purpose`, obligatorio;
- `owner_pid` y `owner_created`, la creación canónica del proceso según `native_process_guard.snapshot`, no un campo nuevo;
- `state` (`waiting` o `claimed`);
- `run_id`, cuando ya lo hay;
- `created_at` y `touched_at`, en epoch UTC;
- `ttl_s`: 120 en `waiting` y 600 en `claimed`.

Purga [S6]: una entrada vencida por ttl, o cuyo titular salió. El titular salió si su PID no existe (`process_not_found`) o si el PID está vivo con otra creación. Con `identity_unavailable` (por ejemplo AccessDenied), la entrada se queda y la caja sigue ocupada.

Admisión [S3][S4]: toda admisión de un arranque nuevo se decide bajo `box_admission()` (`box_admission.py`), que ya serializa entre instancias. Eso vale para `start_run`, sea por la cola, inmediato o por lifecycle directo. Dentro de esa sección:
- Solo admite el arranque que trae el `ticket` exacto de la cabeza, en `claimed` y sin vencer. Un arranque sin ticket se rechaza si la cola no está vacía: es un `active_run_exists` con `audit_reason="box_queue_ahead"`, sin adelantar a nadie.
- Al admitir, la entrada guarda el `run_id`. Mientras el run vive, la entrada sigue, porque el claim se refresca con el latido existente (`server.py:6551`), que ahora lee la respuesta. Al acabar el run o la llamada, se borra.
- Si el claim se pierde (latido tardío, ttl), la llamada aborta o se vuelve a poner en la cola dentro de su presupuesto original. Nunca lanza con un claim perdido.

`box_occupancy` y `session_status.box`:
- `box.occupied` es True si hay una entrada `claimed` que no es del caller, o la cola no está vacía y el caller no es la cabeza.
- `box.queue` proyecta la FIFO común (instancia, holder, purpose, waiting_s; sin tokens).
- `box.foreign_holder` se proyecta también para un turno externo sin run [S9]: `instance="external"`, `holder`, `purpose` y `run_id=null`. Esto cambia el requisito de `run_id` de `box_holder.py:299` solo para `kind=external`.

Fichero ilegible o de un esquema desconocido [S7]: la caja queda ocupada (`reason="box_queue_unreadable"`), sin reset por edad. Se recupera a mano, como `m1.espera`: la negativa nombra el fichero, y `admin_cli` (TTY, motivo y confirmación, la ruta que ya existe) lo rehace vacío y deja audit. Un `takeover` tampoco desaloja nada con la cola ilegible: se rechaza antes de `execute_dayz_test_stop` (`server.py:7920`).

### 3B. `python -m dayz_mcp.box_turn`: solo `status` y `run` [S8]

Funciona sin cliente MCP, sin keyfile y sin arrancar daemon.

- `status --json`: devuelve `occupied`, `reason`, `holder` y `queue`. Usa las mismas sondas de solo lectura (DayZ por nombre y por UDP, `m1.espera`, la cola común). Un puerto TCP que solo escucha no cuenta. Un escaneo indeterminado da `occupied`.
- `run --purpose TEXTO [--holder NOMBRE] [--wait-s N] -- COMANDO...`:
  - se pone en la cola común y espera a ser cabeza con la caja libre (sin DayZ vivo, sin `m1.espera` y sin otra `claimed`);
  - pasa a `claimed`, lanza el comando en primer plano, refresca el claim mientras el comando viva y suelta en un `finally`;
  - el comando tiene que durar toda la partida: para un DayZ lanzado por WMI, el comando es un script que espera, por handle, a que salga ese PID tras comprobar su identidad.
- Salida: el comando sale con su propio código y `box_turn` lo devuelve tal cual. Antes de lanzarlo, `box_turn` sale con 2 (argumentos), 75 (no llegó el turno en `--wait-s`) o 70 (cola ilegible o escaneo indeterminado). Para distinguir un 75 propio de uno del comando [S9], `box_turn` escribe al final una línea JSON en stderr: `{"box_turn": "ran" | "wait_expired" | "bad_args" | "unreadable", "exit": N}`.

### Criterios de la fase 3

Tests con un `shared_root` temporal y, donde se dice, procesos Windows reales independientes (no dos coordinadores en el mismo proceso [S5]), con los spawns de DayZ simulados:

- F3-pos1 [S3]: con un run vivo en la instancia 1, se ponen en cola B (instancia 2) y C (instancia 1). Al acabar el run, un arranque inmediato sin ticket intenta colarse en el hueco y es rechazado con `box_queue_ahead`. Entra B, y C cuando B suelta.
- F3-pos1b [S3]: si falla la escritura del alta de C y C lo reintenta, el orden queda B y luego C, sin duplicados.
- F3-pos2 [S6]: titular con PID y creación que coinciden: la entrada se queda. Proceso muerto: se purga. PID vivo con otra creación: se purga. AccessDenied: se queda y la caja sigue ocupada.
- F3-pos3 [S9]: con un run MCP vivo, `box_turn run --purpose t --holder hb -- python -c "espera"` espera. Al acabar el run, corre. Mientras corre, sin ningún DayZ, `session_status` de otra instancia da `occupied` con `foreign_holder.holder="hb"` y `purpose="t"`, y su `dayz_test_run` con `on_busy="queue"` espera. Al salir el comando, la caja queda libre.
- F3-pos4: con un socket TCP escuchando y sin DayZ, `box_turn status --json` da `occupied: false`. Con `m1.espera` o con un escaneo indeterminado, `occupied: true`.
- F3-pos5 [S5]: dos procesos daemon de instancias distintas con el mismo root temporal: el segundo `on_busy="queue"` espera detrás del run del primero y no lanza mientras dure. Hay contención del lock, un intento de borrar el lock abierto que falla, y un fallo de `os.replace` que deja el documento anterior. Todas las esperas son acotadas.
- F3-claim [S4]: el claim en el borde 599/600 s. Con un latido tardío A pierde el claim durante el build, B coge y suelta, A se reanuda: A tiene cero spawns y se vuelve a poner en la cola o aborta.
- F3-sib [S4][S6]: un ticket hermano de la misma sesión no reclama ni suelta el de otro. Otra sesión del mismo daemon (mismo PID) tampoco.
- F3-neg1 [S7]: un fichero truncado reciente, uno antiguo, uno de versión desconocida y uno con titular activo: en todos `occupied` con `box_queue_unreadable`, cero spawns y cero desalojos, también con `takeover=true`.
- F3-neg3: `box_turn run` sin `--purpose` o con él vacío sale con 2, no escribe nada y deja la línea `bad_args`.
- F3-neg4 [S8]: si `box_turn run` muere a la fuerza, su entrada se purga por titular salido. Si deja un DayZ hijo vivo, la caja sigue ocupada por la sonda de DayZ hasta que el hijo salga.
- F3-exit [S9]: un comando que sale con 75 da exit 75 y la línea `ran`. Una espera agotada da 75 y `wait_expired`.
- F3-reg: siguen en verde la suite de la cola local (`on_busy`, reencolado, `m1.espera`) y la de `box_admission`.

Prueba en el PC (después del merge, sin tocar lo ajeno):
- `box_turn status --json` con un arnés de Grok Bot abierto da libre.
- Un `box_turn run` corto desde una sesión de agente comprueba que el clasificador del PC no lo para.
- Con la caja ocupada por LIVE, un `box_turn run` espera y entra después.

### Docs (en la PR de 3)

- La regla de bolsillo de #466 añade: «fuera del MCP, `python -m dayz_mcp.box_turn run --purpose ... -- ...`».
- `tools/README-mcp.md` junto a la línea 439.
- CHANGELOG.

## Orden y reparto

1. Fase 3A (Grok implementa en el PC, revisa Sol).
2. Fase 2 v3: R22 de Sol sobre su sección y luego el mismo reparto.
3. Fase 3B, con el mismo reparto.
4. Texto para Guillermo para LFPG, dsbx y Grok Bot (envolver con `box_turn run`, un turno por partida), tras la prueba en el PC.
