# Plan: una cola de la caja para todos (fases 2 y 3)

Pedido por Guillermo el 27/11 («Todo lo P0, por fases»), sobre la consultoría de Neon (solo lectura, main 1e105d1; informe en el PC, `C:\temp\neon-cola-1127\INFORME-neon.md`, sha256 ac35ed65…059374). La fase 1 (docs) es #466. Este plan cubre las fases 2 y 3. Cada fase es una PR aparte, Python y docs, sin PBO ni Enforce.

Problema que traza cada cambio (detonantes reales):

- P-a: la cola de la caja vive en la RAM de cada daemon (`session_coordination.py:420`, `box_wait_touch` :2559). LIVE, experimental, xai y los daemons de los arneses de Grok Bot (8797, 8801) no ven las colas de los demás. Sus relevos no dejan hueco y el de otro daemon espera para nada (12/11; 27/11 en el portátil).
- P-b: lo que se lanza fuera del MCP no puede pedir turno: el cliente B del #432, LFPowerGrid y el banco dsbx. La única señal es `m1.espera`, binaria y sin titular (`box_reservation.py`).
- P-c: un lease sin run se renueva siempre con solo latir (`daemon.py:170-171` devuelve False → `session_coordination.py:1473` no niega). Es la ficha 37a3, pendiente desde #391.

Fuera de alcance: Workbench (P2, decide Guillermo), el exit 75 por cwd, tocar LFPG, dsbx o Grok Bot (lo hace Guillermo con el texto que le demos), y quitar `m1.espera` (se queda tal cual).

## Fase 2: el lease sin run también caduca (P-c)

Cambio en `daemon.py` `idle_hold_probe`: si el lease no tiene ningún run propio en `RUNNING`, devuelve True (hold) solo cuando además:

1. no tiene ningún run propio en `STARTING`, `STOPPING` ni `UNRECONCILED`, y
2. no hay una operación `dayz_test_run` ni `dayz_test_stop` en curso con ese lease. La trampa de #391: el launcher nativo late mientras espera al build lock o al servidor, antes de que exista el run. La señal la tiene que dar el propio launcher, por ejemplo un `purpose` `dayz-test` del lease o un marcador de operación activa. El implementador elige la señal que ya exista y lo dice; si no existe ninguna, para y lo pregunta.

El umbral es el mismo `LEASE_WORK_IDLE_S` (1800 s), sin constante nueva.

Criterios (tests unitarios con reloj inyectado, en `tools/tests/`):

- F2-pos: un lease sin run que solo llama a `session_heartbeat` durante 1800 s recibe `renewal_refused="work_idle"`, y a los 120 s siguientes el siguiente de la FIFO del lease obtiene el lease.
- F2-neg1: un lease sin run que manda un comando con su token dentro de la ventana sigue renovando (`decision` distinta de `work_idle`).
- F2-neg2: un lease del launcher de `dayz_test_run` esperando al build lock 1900 s, sin run, sigue renovando.
- F2-neg3: un lease con run propio `STARTING` sigue renovando.
- F2-reg: los tests de #391 (lease con run `RUNNING`, entrada humana) siguen en verde sin cambios.

## Fase 3: una cola común entre instancias y un turno para lo de fuera (P-a, P-b)

### 3A. Fichero de cola compartido

`%LOCALAPPDATA%\DayZ_MCP_shared\box-queue.json`, mutado solo bajo `box-queue.lock`, con `byte_lock.open_lock_file`/`try_lock`: reintento corto acotado, nunca una espera sin tope. Se escribe con `runtime_state.atomic_write_json`. El módulo nuevo es `tools/dayz_mcp/shared_box_queue.py`, con funciones puras sobre el documento y una capa fina de E/S.

Cada entrada tiene:
- `ticket` (aleatorio);
- `kind` (`mcp` o `external`);
- `instance`;
- `holder`, que es el session id público o el nombre que da el externo;
- `purpose`, obligatorio y no vacío;
- `pid` y `pid_created` del proceso dueño: el daemon, o el `box_turn`;
- `state`, que es `waiting` o `active`;
- `created_at` y `touched_at`, en epoch UTC;
- `ttl_s`: 120 en `waiting` y 600 en `active`, refrescados con latido.

Se purga una entrada cuando vence su ttl o cuando su `pid` con `pid_created` ya no existe.

Integración mínima, sin reescribir la cola local:
- `box_wait_touch` sigue igual, y además mantiene una entrada `mcp` del daemon por cada ticket local. Al hacer `claim`, la entrada pasa a `active` solo si es la primera de la cola global y no hay otra `active`.
- `_box_ready_for` (`box_occupancy.py:67`) exige además ser cabeza global. `box.occupied` es True si hay una entrada `active` que no es suya; `box.foreign_holder` la nombra.
- `start_run` (`process_lifecycle.py` junto a `box_blocks_start`, :5432) rechaza con `active_run_exists` y `audit_reason="box_turn_held"` si hay una `active` ajena.
- `session_status.box.queue` añade las entradas globales de otros, con `instance`, `holder`, `purpose` y `waiting_s`, sin tokens.

Fail-closed: un fichero ilegible o con un esquema desconocido se lee como caja ocupada (`reason="box_queue_unreadable"`), igual que `m1.espera` ilegible. Pero no se bloquea para siempre: un fichero ilegible con más de 600 s sin cambios lo puede reescribir vacío cualquier escritor, que deja una línea de audit.

### 3B. `python -m dayz_mcp.box_turn`

Funciona sin cliente MCP, sin keyfile y sin arrancar daemon. Usa `shared_box_queue` y el mismo sondeo de solo lectura de DayZ que ya usa `box_occupancy`.

Subcomandos:
- `status --json`: devuelve `occupied`, `reason`, `holder` y `queue`. Un puerto TCP de un daemon que solo escucha no cuenta.
- `run --purpose TEXTO [--wait-s N] -- COMANDO...`: se pone en la cola, espera a ser cabeza con la caja libre (sin DayZ vivo, sin `active` ajena y sin `m1.espera`), pasa a `active`, lanza el comando y late mientras dure. Al acabar el comando suelta en un `finally` y devuelve el exit del comando.
- `acquire` y `release --ticket`: para quien no puede envolver su lanzamiento, por ejemplo un DayZDiag que se lanza por WMI y vive más que el comando. `acquire` deja un proceso latiendo en segundo plano hasta `release` o hasta que muera su padre.

Los códigos de salida son: 0, el del comando, 2 (argumentos), 75 (no llegó el turno en `--wait-s`) y 70 (cola ilegible). Contención no es fallo: 75 se documenta como reintentable.

### Criterios de la fase 3

Tests con un `shared_root` temporal y dos coordinadores (dos instancias) en el mismo proceso, más un subproceso real para `box_turn`:

- F3-pos1, orden entre instancias: con un run vivo en A, se ponen en cola B (instancia 2) y luego C (instancia 1). Al acabar A, entra B y C espera. C entra cuando B suelta.
- F3-pos2, dueño muerto: si B muere sin soltar (pid falso muerto), C sube y entra en la siguiente purga.
- F3-pos3, externo: con un run MCP vivo, `box_turn run --purpose t -- python -c "..."` espera. Cuando el run acaba, corre, y mientras corre un `dayz_test_run` de otra instancia ve `occupied` con `holder` = t. Al salir, la caja queda libre.
- F3-pos4, puerto ocioso: con un socket TCP escuchando y sin DayZ, `box_turn status --json` da `occupied: false`.
- F3-pos5, misma máquina (caso del portátil, 27/11): dos daemons de instancias distintas con la misma caja: el segundo `on_busy="queue"` espera detrás del run del primero y no lanza mientras dure.
- F3-neg1: un fichero truncado da `occupied` con `box_queue_unreadable`, y ningún `start_run` admite. Con más de 600 s sin cambios se rehace y se puede volver a lanzar.
- F3-neg2: una sesión no puede soltar ni reclamar el ticket de otra (otro holder o pid): se rechaza y la entrada sigue.
- F3-neg3: `box_turn run` sin `--purpose` o con él vacío sale con 2 y no escribe nada.
- F3-neg4: si `box_turn run` muere a la fuerza, su entrada se purga por pid y no deja la caja bloqueada.
- F3-reg: la suite de la cola local (`on_busy`, reencolado, `m1.espera`) sigue en verde.

Prueba en el PC (después del merge, con lease y sin tocar lo ajeno):
- `box_turn status --json` con el arnés de Grok Bot abierto da libre.
- Un `box_turn run` corto desde una sesión de agente: se comprueba que el clasificador del PC no lo para.
- Con la caja ocupada por LIVE, un `box_turn run` espera y entra después.

### Docs (en la misma PR de 3)

- La regla de bolsillo de #466 añade: «fuera del MCP, `python -m dayz_mcp.box_turn run --purpose ... -- ...`».
- `tools/README-mcp.md` junto a la línea 439.
- CHANGELOG.

## Orden y reparto

1. Fase 2: implementa Muse o Grok (delegar); revisa Sol (Neon si Sol no puede). Tests en el PC, Windows.
2. Fase 3A y 3B, en una PR o en dos (3A primero), igual reparto.
3. Texto para Guillermo para LFPG, dsbx y Grok Bot (envolver con `box_turn run`, un turno por partida), tras la prueba en el PC.
