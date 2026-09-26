# laneFENCE — fencing por `instance` (D-55, v1)

## 1. Plan de implementación

Contrato: cabecera del spec manda sobre el cuerpo. Código real manda sobre ambas
cuando hay desfase de líneas. No se toca OneDrive/`P:\`, ni el daemon vivo, ni
DayZ, ni `MCP_BRIDGE_VERSION` / `EXPECTED_BRIDGE_VERSION`.

### Código real que el spec cita mal (sigo el fichero)

- `ProcessLifecycle.start_run` vive en `process_lifecycle.py:1112`, no `:862`.
  `self.launcher(...)` está en `:1304-1308`, no `:1046-1050` (eso es
  `_settle_failed_launch`).
- `stop_run` `:1530`, `adopt_run` `:1777`, `reap_dead_runs` `:2219`,
  `reap_dead_run` `:2231`, `_reap_run_locked` `:2176`.
- `record_poll` `:1011`, `_enqueue_command` `:765`, `store_result` `:1246`,
  `status_snapshot` `:1457`, `_handle_poll` `:1944`, `_handle_result` `:1960`.
- `VALID_PEERS` `:72`, `COMMAND_TTL_S` `:102`, `PEER_RECONNECT_GAP_S` `:103`.
- `_connected_server_pid` en `accredited_daemon_transport.py:78-113` (reuso
  tal cual sobre `Handler.connection`: `getsockname`/`getpeername` invertidos
  respecto del cliente MCP, y eso es justo el dueño DayZ del socket).
- `compute_bridge_ready` `server.py:192`, `READY_REASONS` `:82-89`,
  `_REMOTE_ERROR_CODES` `:91`.
- `ProcessLifecycle(...)` se construye en `daemon.py:508-525`; el cableado
  `state.lifecycle = lifecycle` está en `:577`.

### Diseño

1. `dayz_mcp/instance_fence.py` (nuevo): `BindingRegistry` Protocol, dataclass
   `Binding`, estados, hints literales de §5.3, `instance_prefix`,
   `BindingPrepareError`. Citas de `_valid_uuid4` importadas de
   `process_lifecycle.py:41`.
2. `ServerState` implementa el Protocol bajo `self._lock`:
   - `_legacy_queues` (alias `_queues` para no romper lecturas existentes)
   - `_bound_queues: {instance: []}`
   - `_bindings`, `_role_index`, `_retired`, `_station_epoch`
   - `_bound_last_poll_at`, `_command_fence`, `_pid_attr_cache`
   - `prepare` / `confirm` / `retire_role` / `retire_run`
   - `install_bound_peer` solo para tests (mismo estado final que confirm)
3. `/poll`: `record_poll(peer, version=None, instance=None, source_pid=None)`.
   Sin `inst=` → camino legacy (lecturas, flush BUG-041, TTL). Con `inst=`
   bound + `source_pid` que casa → cola bound, **sin** reconnect-flush, **con**
   TTL. HTTP 200 y `commands: []` en todo rechazo. Ocupación
   (`_last_poll_at`) siempre; `_bound_last_poll_at` solo acreditado.
4. Atribución TCP: `_handle_poll` resuelve PID **con el socket vivo** reusando
   `_connected_server_pid`. Cache `(instance, pid)` con recheck 2.0 s
   (precisión de la cabecera: no barrer la tabla en cada poll). Los unitarios
   inyectan `source_pid=` y no tocan TCP.
5. `/enqueue`: tras el version gate, mutación exige exactamente un binding
   `BOUND` del peer (o `offline` que cubre ambos). Lectura → bound si hay
   BOUND, si no `_legacy_queues`. Sidecar `_command_fence[id]=(instance,epoch,pid)`.
   409 + `hint` literal. `internal=True` (`vehicle_release`) apunta al instance
   bound del cliente.
6. `/result`: `_handle_result` lee `inst=` y lo pasa a `store_result`. Inst
   distinta → 200 + `discarded` + audit `late_result_fenced` (prefijos, no UUID).
   Mismo instance y epoch viejo → se acepta, audit `late_result_same_instance`.
7. `status_snapshot` aditivo: `binding_state`, `instance_prefix` (8 hex),
   `bound_last_poll_age_s`. `queue_depth` = cola que ese rol puede vaciar.
8. `compute_bridge_ready`: si hay binding, liveness sale de
   `bound_last_poll_age_s`. Razones nuevas `binding_ambiguous` y
   `unbound_after_restart`. `_REMOTE_ERROR_CODES` gana los códigos de §5.3.
9. `ProcessLifecycle`: kwarg opcional `bindings=None`. `start_run` antes de
   `launcher`: `retire_role` del rol que se lanza, `prepare` (mint+write
   atómico del `$profile:dayz_mcp.json` de **ese** rol; no toca `$mission:`),
   Popen, `confirm`. Fallo de prepare/Popen/snapshot → `retire` del UUID
   mintado, no se lanza si prepare falla. `stop_run` éxito y
   `_reap_run_locked` éxito → `retire_run`. `adopt_run` no toca bindings.
10. `daemon.py`: `ProcessLifecycle(..., bindings=state)`.
11. Enforce aditivo, versión intacta `"8"`: `MCPConfig.instance`,
    `m_PeerInstance` (no `m_Instance`), copia en `TryInit`, `inst=` en
    `StartPoll`/`PostResult` solo si no vacío, `ReloadKeyAfterFailure` no se
    toca. No se valida UUID en Enforce.

### Tests primero

`tools/tests/test_instance_fence.py` con los 23 de §8.1. Extiendo
`test_poll_key_reload_contract.py` para exigir `m_PeerInstance` en la clase y
su ausencia en `ReloadKeyAfterFailure`. No borro `StaleCommandHygieneTest`.

Los tests que ya pasarían hoy (lectura legacy, versión `"8"`, reload que ya
no toca identidad) se anclan a un símbolo **nuevo** (`_legacy_queues`,
`string instance;`, miembro `m_PeerInstance`) para poder ponerse rojos.

### Fuera de v1 (no se escribe)

Cola de caja, movimiento 1, bump 8→9, persistir bindings, rebind tras
`adopt_run`, lock de escritorio, tools de click físico, validar UUID en
Enforce, tocar worker/bundle/`session_coordination`/`runs.json`/`install_mcp`.
Canario in-game §8.2: no se ejecuta (frontera: no lanzar DayZ).

### Desviaciones respecto de este plan

- `_queues` se queda como alias de `_legacy_queues` (los tests de higiene BUG-041
  y varios asertos de `_queues["server"]` siguen leyendo la cola por rol).
- `instance_fence.py` no importa `process_lifecycle` (ciclo de import). El
  Protocol usa `record: object`. `_valid_uuid4` se importa dentro de
  `prepare` / `install_bound_peer`.
- `ProcessLifecycle` habla con el registry por métodos directos
  (`bindings.prepare` etc.), no `getattr`: el auditor de HTTP marca
  `getattr` como `dynamic_http`.
- TTL de `COMMAND_TTL_S` corre sobre **todas** las colas en cada poll, no
  solo la de entrega. Si no, un poll legacy dejaría vivir un comando bound
  más allá del TTL.
- `install_bound_peer` (solo tests) rellena `_forced_poll_pids` para que un
  `FakePeer` HTTP pueda atribuir PID sin tabla TCP. `prepare`/`confirm` de
  producción no lo tocan; el handler real usa `_connected_server_pid` con el
  socket vivo.
- Canario in-game §8.2 no se ejecutó (frontera: no lanzar DayZ).

## 2. Qué quedó implementado, por fichero

Puntos de entrada nuevos (líneas del árbol de esta copia, reabiertas):

| Fichero | Qué |
|---|---|
| `DayZ_MCP\scripts\5_Mission\MCPMessages.c:10` | `string instance;` en `MCPConfig`. `MCP_BRIDGE_VERSION` sigue `"8"`. |
| `DayZ_MCP\scripts\5_Mission\MCPBridge.c:32,67,183-186,204,228-230,3295-3297` | `m_PeerInstance`; copia en `TryInit`; `inst=` en `StartPoll`/`PostResult` si no vacío. `ReloadKeyAfterFailure` no se toca. |
| `DayZ_MCP\scripts\5_Mission\MCPClientBridge.c:130,167,299-302,349-351,3117-3119` | Espejo del servidor. |
| `DayZ_MCP_dev\tools\dayz_mcp\instance_fence.py:71-74,86-104` | Protocol, `Binding`, hints literales §5.3, `fence_error`, `instance_prefix`. |
| `DayZ_MCP_dev\tools\dayz_mcp\loopback.py:589,635,649,657,666,1335,1653,2436,2464` | `prepare`/`confirm`/`retire_*`/`install_bound_peer`; `record_poll(..., instance, source_pid)`; `store_result(..., instance)`; `_handle_poll` atribuye PID; `_handle_result` lee `inst=`. |
| `DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py:798,816,1157,1350,1363,1827,1841,2268` | kwarg `bindings`; `start_run` hace `prepare` **antes** de `launcher` (`:1363`); `adopt_run` no confirma; `stop_run`/`_reap_run_locked` llaman `retire_run`. |
| `DayZ_MCP_dev\tools\dayz_mcp\daemon.py:525` | `ProcessLifecycle(..., bindings=state)`. |
| `DayZ_MCP_dev\tools\dayz_mcp\core.py:72-74` | `binding_state` / `instance_prefix` / `bound_last_poll_age_s` en `_peer_status`. |
| `DayZ_MCP_dev\tools\dayz_mcp\server.py:82-90,126-135,209` | `READY_REASONS` gana `binding_ambiguous` y `unbound_after_restart`; códigos §5.3 en `_REMOTE_ERROR_CODES`; `compute_bridge_ready` usa `bound_last_poll_age_s` si hay binding. |

No se tocó: `session_coordination.py`, `dayz_test_worker.py`, `install_mcp.py`, `runs.json`/`RunRecord`, `native-launchers`, `MCP_BRIDGE_VERSION`, `EXPECTED_BRIDGE_VERSION`.

## 3. Tabla de tests nuevos (§8.1)

`red_first.log`: `Ran 1645 tests` / `FAILED (failures=14, errors=15, skipped=6)` — los 23 nuevos en rojo (FAIL o ERROR). `green.log`: los 23 en verde.

| Test | Si se rompe… | rojo `red_first` | verde `green` |
|---|---|---|---|
| `test_record_poll_without_instance_does_not_deliver_mutation` | un `player_teleport` encolado se entrega a un poll sin `inst=` | sí | sí |
| `test_record_poll_without_instance_still_delivers_read` | `query_player_state` deja de llegar por legacy | sí | sí |
| `test_enqueue_mutation_without_binding_is_legacy_unbound` | enqueue de mutación sin binding no da 409 `legacy_unbound` | sí | sí |
| `test_bound_poll_receives_only_its_instance_queue` | poll `inst=C1` recibe un comando sellado a C2 | sí | sí |
| `test_role_mismatch_does_not_drain_server_queue` | poll `peer=client&inst=<server>` vacía la cola del server | sí | sí |
| `test_offline_instance_may_poll_both_peers` | un binding `offline` deja de recibir server y client reads | sí | sí |
| `test_reload_key_after_failure_does_not_assign_peer_instance` | `ReloadKeyAfterFailure` asigna `m_PeerInstance` | sí | sí |
| `test_try_init_copies_instance_once_in_both_bridges` | `TryInit` no copia o `StartPoll` no manda `inst=` | sí | sí |
| `test_start_poll_omits_inst_when_peer_instance_empty` | un puente sin instance envía `inst=` vacío | sí | sí |
| `test_reconnect_gap_does_not_flush_bound_queue` | un hueco > `PEER_RECONNECT_GAP_S` en BOUND descarta su cola | sí | sí |
| `test_reconnect_gap_still_flushes_legacy_queue` | el flush BUG-041 deja de vaciar `_legacy_queues` | sí | sí |
| `test_command_ttl_still_expires_bound_command` | un comando bound más viejo que `COMMAND_TTL_S` se entrega | sí | sí |
| `test_second_pid_same_instance_marks_ambiguous` | dos `source_pid` con el mismo `inst=` siguen recibiendo mutaciones | sí | sí |
| `test_unattributed_poll_gets_zero_commands` | `source_pid is None` entrega la cola bound | sí | sí |
| `test_store_result_wrong_instance_is_discarded_200` | POST `/result` con `inst` ajeno escribe el resultado | sí | sí |
| `test_adopt_run_does_not_confirm_binding` | `adopt_run` deja el run `BOUND` sin prepare/confirm | sí | sí |
| `test_start_run_writes_instance_before_popen` | `launcher` corre sin UUID en el JSON del perfil | sí | sí |
| `test_start_run_does_not_write_mission_config` | `start_run` muta `$mission:dayz_mcp.json` | sí | sí |
| `test_client_relaunch_retires_old_instance_keeps_server` | tras relanzar client, poll C1 muta o el server pierde binding | sí | sí |
| `test_status_hides_full_instance` | `/status` serializa el UUID de 36 caracteres | sí | sí |
| `test_ready_uses_bound_last_poll_not_legacy` | un poll legacy mantiene `ready=true` con bound stale | sí | sí |
| `test_version_gate_unchanged_for_v8_without_inst` | `ver=8~x` sin `inst=` pasa a `version_blocked` | sí | sí |
| `test_expected_bridge_version_stays_8` | alguien sube `EXPECTED_BRIDGE_VERSION` / `MCP_BRIDGE_VERSION` | sí | sí |

Todos los 23 de la spec se escribieron. Extensión de
`test_poll_key_reload_contract.py`: el cuerpo de `ReloadKeyAfterFailure` no
contiene `m_PeerInstance` (rojo en `red_first` porque el miembro aún no
existía; verde al final). `StaleCommandHygieneTest` no se borra: cubre el
camino **legacy**.

`test_task9_spawn_phase_markers` (`test_full_source_hash_is_frozen`,
`test_removing_only_marker_lines_restores_frozen_source_hash`): rojo
esperado en `green.log`. No se re-congela.

### 3.b Tabla A/B de tests **base** tocados

Criterio: A = el test describía colas por rol / mutación sin identidad; se
adapta el **arreglo** (bind + poll acreditado) y se conservan los asertos de
garantía. B = defecto de implementación → se arregla código.

Ningún aserto de autoridad se relajó, no hay `skipTest`, no hay timeouts
subidos, no hay `try/except` para tragar fallos.

**A — el test hablaba del mundo viejo; la garantía vive en el mismo test
(más, donde se indica, un test nuevo de §8.1).**

| Fichero / test | Antes | Ahora | Garantía y dónde vive |
|---|---|---|---|
| `test_loopback.LoopbackTest` (setUp + `/poll`/`/result` con `inst=`) | enqueue/poll HTTP sin identidad | `bind_both_peers` + `inst=` en poll/result | Round-trip de 5 endpoints y routing: **este test**. Mutación sin `inst=` no entrega: `test_record_poll_without_instance_does_not_deliver_mutation`. |
| `test_loopback.OwnerScopedQueueStateTest` | `record_poll("server")` entregaba mutaciones | `accredited_poll` | Pin de owner, lease, cancel, TTL, race authorize/release: **los mismos tests**. |
| `test_loopback.test_internal_vehicle_release_*` | `vehicle_release` interno en `_queues["client"]`; reconnect flush lo tiraba | cola bound; TTL sigue caducando; reconnect **no** flush (D-55.5) | Fire-and-forget se purga: TTL test. Un proceso **otro** no hereda: `test_reconnect_gap_still_flushes_legacy_queue` + `test_bound_poll_receives_only_its_instance_queue`. El reconnect-flush bound se **retira a propósito**; no es pérdida de BUG-041 (ese bug era identidad por rol). |
| `StaleCommandHygieneTest` | sin cambios | sigue en legacy | BUG-041 sobre `_legacy_queues`: **este test**. |
| `test_inventory_give` / `test_player_teleport` / `test_object_anim` / `test_vehicle_prepare_fixture` / `test_vehicle_trace_contract` / `test_restore_gameplay_contract` setUp | enqueue mutación 200 sin binding | `bind_both_peers` | Ingress (whitelist, peer, args): **el mismo test**. 409 sin binding: `test_enqueue_mutation_without_binding_is_legacy_unbound`. |
| `test_mcp_server` | poll HTTP sin `inst=` recibía `camera_set` | bind + `inst=` | Routing two-peer: **este test**. |
| `test_mcp_tools.FakePeer` + `build_started` | poll/result sin `inst=` | `inst=` + bind del runtime | Tools FastMCP: **los mismos tests**. |
| `test_client_mode.GamePeer` | igual | `inst=` en GET `/poll` y POST `/result` | Client-mode round-trip: **este módulo**. |
| `test_daemon.DaemonHttpServer` | ServerState desnudo | `bind_both_peers` tras `build_server_state` | Fixtures daemon: **este módulo**. |
| `test_session_http` / `test_session_e2e` | poll por rol | bind + `inst=` | Sesión/lease e2e: **estos tests**. |
| `test_fase4b_loopback.start_server` | mutación 200 sin bind | bind | Whitelist world_time/weather: **este test**. Exec 403 sigue **antes** del fence. |
| `test_fase4b_tools` / `test_x5_tools.build_started` | igual | bind del runtime | Tools X5/Fase4B: **estos tests**. |
| `test_x5_loopback.test_http_exec_audit_failure_*` / `test_exec_queue_full_*` | exec 503/429 sin identidad | bind, luego 503/429 | Audit fail = 503 y no encola: **este test**. Queue-full sin `allowed` fantasma: **este test**. |
| `test_x5_loopback` version-gate (`test_delivery_gate_*`, `test_without_validator_*`) | **no se tocó el start_server de esos casos** (no bind) | lecturas van por legacy | Version gate no drena cola: **estos tests**. |
| `test_retail_quarantine` | mutación 200 tras levantar cuarentena | bind | Retail bloquea mutación, lectura pasa: **este test**. |
| `test_python_backlog_fixes.test_bug025_*` | exec_enforce sin bind | bind | Audit I/O no bloquea el event loop: **este test**. |
| `test_poll_key_reload_contract` | solo `m_Key` | además: existe `m_PeerInstance` y Reload no lo toca | Key ≠ identidad: **este test** + `test_reload_key_after_failure_does_not_assign_peer_instance`. |
| `test_task7_*` enqueue | mutación 200 sin bind | `bind_both_peers` | Enqueue con lease: **el mismo test**. |
| `test_task7_*` `record_poll("server")` | entregaba la cola por rol | `accredited_poll(state, "server")` | Despacho (claim vs release, retail en poll, probe fuera del lock, expiry antes de claim, read no llama retail): **el mismo test**, ahora contra la cola bound. Sin esto el test se ponía verde **en falso** (poll legacy vacío mientras la mutación seguía en `_bound_queues`). |
| `test_last_queue_slot_cannot_emit_phantom_allowed_audit` | el id `allowed` estaba en `_queues["server"]` | el id está en la cola **bound** (no en legacy) | Un solo `allowed` y el comando **está publicado** en una cola real: **este test**. |
| `test_cancel_pending_fences_exec_allowed_before_publish` | `_queues["server"] == []` | además `bound_queue(server) == []` | Exec cancelado no queda en ninguna cola: **este test**. |

**B — defecto de implementación, se arregló código (no el aserto).**

| Qué | Cómo se veía | Arreglo |
|---|---|---|
| TTL bound no corría si el poll era legacy | un comando bound podía sobrevivir `COMMAND_TTL_S` mientras un peer ajeno sondeaba el rol | `record_poll` caduca **todas** las colas en cada poll |
| `getattr(bindings, "prepare")` | `test_productive_runtime_closure_has_no_unaccredited_http_path` rojo (`dynamic_http`) | llamadas directas `bindings.prepare` / `confirm` / `retire_*` |

**No es A ni B, rojo esperado:** `test_task9_spawn_phase_markers` (centinela SHA de `MCPBridge.c`).

**No apareció en `green.log`:** `test_bug046_startup_deadlock` (había salido en `red_first` por carga; en la suite final pasó). No se tocó.

**Autocrítica ronda 1.** `bind_both_peers` sin cambiar `record_poll` en task7 habría dejado esos tests **verdes de mentira** (poll legacy `commands=[]` con la mutación intacta en la cola bound). Eso se corrigió en ronda 2: el poll del test es el acreditado y los asertos de claim/retail/probe siguen midiendo lo mismo. El cambio del reconnect de `vehicle_release` interno (flush → entrega al mismo instance) es A documentada, no un aserto bajado.

## 4. Contradicciones spec vs código real

| Spec (cuerpo) | Código real | Qué seguí |
|---|---|---|
| `start_run` `:862`, `launcher` `:1046-1050` | `start_run` `process_lifecycle.py:1157`; `launcher` `:1363`; `:1046` era `_settle_failed_launch` en el árbol de la spec | código |
| `stop_run` `:1272`, `adopt_run` `:1519`, `reap_dead_runs` `:1961` | `:1593`, `:1841`, `reap_dead_runs` más abajo; `retire_run` en `:1827` y `:2268` | código |
| `record_poll` `:936`, `_enqueue_command` `:690`, `store_result` `:1171`, `_handle_poll` `:1855` | al terminar: `record_poll` `:1335`, `_enqueue_command` `:1062`, `store_result` `:1653`, `_handle_poll` `:2436` (el fichero creció) | código |
| Cabecera: atribuir **dentro del handler** y cachear PID | `_handle_poll` `:2436` + `_pid_attr_cache` / `PID_ATTR_RECHECK_S=2.0` | cabecera |
| Cuerpo: barrido TCP en cada poll | no se hace | cabecera |

## 5. LO QUE NO PUDE VERIFICAR

De la spec §10, siguen abiertos:

- **`JsonFileLoader<MCPConfig>` con campo de más / ausente.** No se midió in-game. Un PBO viejo leyendo JSON nuevo con `instance` puede fallar el parseo. Mitigación: `start_run` solo escribe el perfil del proceso que va a lanzar; un proceso vivo no relee `instance` (`ReloadKeyAfterFailure` no toca `m_PeerInstance`). **Igual de peligroso** que en la spec para un PBO viejo que arranque *después* de un `start_run` nuevo; **menos** para un proceso ya configurado.
- **Reutilización de `RestContext` / tabla TCP no unívoca.** Los unitarios inyectan `source_pid=` y no tocan `psutil.net_connections`. El handler de producción sí llama `_connected_server_pid` con el socket vivo; si la tabla no distingue dos clientes, el diseño **bloquea** (`instance_unattributed` / `AMBIGUOUS`), no entrega. El canario §8.2 **no se corrió**. Riesgo **igual** (fail-closed); no hay medida nueva que lo baje.
- **Proceso `offline` carga ambos puentes contra un solo `$profile:`.** El unitario cubre el despacho (`test_offline_instance_may_poll_both_peers`); no hay traza in-game. **Igual**.
- **`capture_screenshot` sin `client_pid`.** Canario no se fía; v1 no lo cambia. **Igual**.
- **`__pycache__` / daemon vivo fuera de esta copia, árbol OneDrive.** No se abrieron. `import dayz_mcp` desde el venv de la copia resuelve a `...\mcp-laneFENCE-20260818\...\dayz_mcp\__init__.py`. `green.log` no contiene `OneDrive`.
- **Atribución TCP en un poll HTTP real de DayZDiag.** Solo el patrón invertido ya en producción y la sonda de la cabecera (2026-08-18). Esta lane no volvió a medir.

No verifiqué: Enforce compilando el PBO; el centinela SHA in-game; que `JsonFileLoader` deje `cfg.instance == ""` cuando el campo falta (el puente lo trata como legacy).

## 6. Riesgo residual

1. **Atribución TCP ambigua o un falso mismo PID para dos DayZ.** Desde fuera: mutaciones dejan de llegar (`instance_unattributed` / `AMBIGUOUS` en `/status`) o —el caso caro— dos clientes copiando el mismo JSON reciben el mismo `inst=` y el segundo pone `AMBIGUOUS` (cero mutaciones a ambos). No se entrega al mundo ajeno. Se vería `ready.reason=binding_ambiguous` y 409 `instance_ambiguous`.
2. **Daemon nuevo + juego ya arriba (PBO viejo o UUID en memoria).** Fail-closed: lecturas legacy sí, mutaciones 409 `legacy_unbound` / `unbound_after_restart`. La partida no se cae; el agente no puede mutar hasta relanzar. Es el coste de la opción A de despliegue.
3. **`_forced_poll_pids`.** Solo lo escribe `install_bound_peer` (tests). Si alguien lo llamara en producción, saltaría la tabla TCP. `start_run` no lo usa.

Si `prepare` escribe `instance` y el loader viejo rechaza el JSON, el síntoma es “el mod no cargó”, indistinguible de un fallo de init. No lo medí.

## 7. Ficheros tocados con sha256

SHA-256 (hex mayúsculas) y tamaño en bytes, relativos a
`C:\Users\guill\AppData\Local\Temp\mcp-laneFENCE-20260818\DayZ Projects\`.

**Producción**

| sha256 | bytes | path |
|---|---|---|
| 2C4B198BD54A6AB73EB9A23FF36A5464DEFC3627DECC2BB9A9EE7920EA2C4673 | 75794 | `DayZ_MCP\scripts\5_Mission\MCPBridge.c` |
| D62E04E81BA05701ED880BA6FAA8199806F80AF7F9C201FEA697D2337834F1F6 | 69666 | `DayZ_MCP\scripts\5_Mission\MCPClientBridge.c` |
| 0096B93E8AD594633727E04E1C44918DAB30E6C0B644304CA662203E1FBDE859 | 9877 | `DayZ_MCP\scripts\5_Mission\MCPMessages.c` |
| C3181F2F441AECCBD217A29036F6C2613B262881AA577E8318CA7E6FE3D9F4DF | 3941 | `DayZ_MCP_dev\tools\dayz_mcp\instance_fence.py` |
| 54F42E283F03F916C7FAA4EDE49FD1B3541243B6101DA1A26B053F5C2BF3E8C6 | 111320 | `DayZ_MCP_dev\tools\dayz_mcp\loopback.py` |
| CE86DBA726BD38514176BD61068E5235E56C822E9DFAE5A4CBC045BBF3AFAE0C | 106402 | `DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py` |
| 7D93516C2697FA4BDD4CCFBFF3F195DBE143B99576333163BB69AFF064671018 | 37224 | `DayZ_MCP_dev\tools\dayz_mcp\daemon.py` |
| 0DEC0663CB62E8263FF543935B28D49713A95FD28BEE4233FB68EEF9DCF4AD27 | 5574 | `DayZ_MCP_dev\tools\dayz_mcp\core.py` |
| AB116B362E958300554E4BA1F2C26F785A58A09DED4570A1D0A095EF433E4DCB | 130571 | `DayZ_MCP_dev\tools\dayz_mcp\server.py` |

**Tests nuevos**

| sha256 | bytes | path |
|---|---|---|
| 34BA31085171424B17253FC78C9FD290CA330FC240B2F3C19B491036836854B3 | 25371 | `DayZ_MCP_dev\tools\tests\test_instance_fence.py` |
| 46F8BDE92F57D9102F4DEC560EE99D3DF3FBCA65B3CDE8101F4C16DF646615D6 | 1291 | `DayZ_MCP_dev\tools\tests\fence_helpers.py` |

**Tests base adaptados (A)**

| sha256 | bytes | path |
|---|---|---|
| BDB7E3EE2A2F83129B63C23C7B95698FE4CE59E7CFCFF859A9D60453E21C98F4 | 48070 | `DayZ_MCP_dev\tools\tests\test_loopback.py` |
| 0E3A13C4FE49558337D51A5D24919FFA68200DD40558A6A8D29D4AB2EA4836D2 | 3592 | `DayZ_MCP_dev\tools\tests\test_poll_key_reload_contract.py` |
| 80E59FFD1E086324B8FFBCACD0552BFAD1B27E3D92B24344F76470A9A62A53A2 | 5636 | `DayZ_MCP_dev\tools\tests\test_inventory_give.py` |
| 943A448AAAF5BCA07E4E8A16A260D2235482899E60F7EA48F0F2C21D1A65D58F | 4997 | `DayZ_MCP_dev\tools\tests\test_player_teleport.py` |
| B4B83F88C35F5CA36812C3E1D2650D317333AC0AD85B2AAA1799C5E1048212F9 | 4827 | `DayZ_MCP_dev\tools\tests\test_object_anim.py` |
| 25D4FD7FAE8FCD00B276B51FD9601068EB309D089342B249C5DEE4CE311AE600 | 5285 | `DayZ_MCP_dev\tools\tests\test_mcp_server.py` |
| 91DCCB7BFBE05632F10FFF7FBB152E539825FCA33D8CEC6D1D52ED35126D45ED | 30085 | `DayZ_MCP_dev\tools\tests\test_mcp_tools.py` |
| 3BA24AC82832047875ADECCA76C2096C3160BB3FA8B66751C2FCBE8AB6E39EBC | 68586 | `DayZ_MCP_dev\tools\tests\test_client_mode.py` |
| 9699C38418CB1313CBA274792CE19453F073ECF607941821DFF33255A5EC50AE | 54996 | `DayZ_MCP_dev\tools\tests\test_daemon.py` |
| AD5FFB08646402A17EEBCD1D06B4925B034F1F032128A69FF3E7303E75506E47 | 25440 | `DayZ_MCP_dev\tools\tests\test_session_http.py` |
| 684B9B3A32BFBAE909FF95D3AA46BC46FCFBC032E350737FF22972318CFA5BBE | 20904 | `DayZ_MCP_dev\tools\tests\test_session_e2e.py` |
| A686B41E4DF10747E03215A3575C00222830D7ED7E853B78E55D37565336D148 | 3506 | `DayZ_MCP_dev\tools\tests\test_fase4b_loopback.py` |
| 1625611DF15216A578392ADDCED5AB4CBEE79D6E60E4BC2D693115C1F6620A1A | 5473 | `DayZ_MCP_dev\tools\tests\test_fase4b_tools.py` |
| 4FB9FC93081489B3512E9C8AA083763AE9761CA1A1F45DA36E9DE681252CD102 | 8910 | `DayZ_MCP_dev\tools\tests\test_x5_loopback.py` |
| 6F4F5DF95B2E1233012A3FA0605120FEF39C317A28402A0422D3335D76ED7B13 | 5874 | `DayZ_MCP_dev\tools\tests\test_x5_tools.py` |
| 19172D39F69A706A841B940903796388DA252442D1D89A085A61996341913F5F | 12669 | `DayZ_MCP_dev\tools\tests\test_python_backlog_fixes.py` |
| 510C333AAA0FAEB869BF92021FFE5D0471B99EFE76781D9DDAF5B193C79127E3 | 7703 | `DayZ_MCP_dev\tools\tests\test_retail_quarantine.py` |
| 1E081693908D43EF845E89B58CAB8E0807F3E14F58F8ABE1E327A78A0C4E48C0 | 6161 | `DayZ_MCP_dev\tools\tests\test_restore_gameplay_contract.py` |
| 9C5129897670A1E2665097341CDE8C131D96D846DFA101EA5C910CA900D7F635 | 6190 | `DayZ_MCP_dev\tools\tests\test_vehicle_prepare_fixture.py` |
| 2C55669C76EA4295CF4BF385277D2013CFCDF560A651A2797BC1D16302B83671 | 8752 | `DayZ_MCP_dev\tools\tests\test_vehicle_trace_contract.py` |
| A4B1FDEE083BC2B5049FFBD6669E5124EE74040745F2DC71AA5FEE640E3D9895 | 71107 | `DayZ_MCP_dev\tools\tests\test_task7_final_authority_regressions.py` |
| 881301B188C0118AF9842071A6938AF97CBD679FEB2EC005CFACF9CC24006174 | 57614 | `DayZ_MCP_dev\tools\tests\test_task7_review_regressions.py` |
| 2A03E2B5A7888722BB7581F6E751D8CEDC0DCEA9CBC066966E8A31A6A60A65E3 | 44213 | `DayZ_MCP_dev\tools\tests\test_task7_rereview_regressions.py` |

Comparado con `baseline.csv`: estos 34 paths y ningún otro bajo `DayZ Projects\`.
`test_task9_spawn_phase_markers.py` **no** se tocó (centinela rojo a propósito).

Informe: `C:\Users\guill\AppData\Local\Temp\mcp-laneFENCE-20260818\laneFENCE-report.md`.
`green.log` (UTF-8 BOM): `Ran 1645 tests in 196.506s` / `FAILED (failures=2, skipped=6)` — los dos fallos son el centinela.

## 8. Ronda 3 — corrección de la revisión

`red_first_r3.log`: 8 tests nuevos, `FAILED (failures=7, errors=1)` contra el código
pre-arreglo. Los dos de CRITICO-2 salieron rojos (cache devolvía el PID
registrado al segundo socket). `green_r3.log`: `Ran 1653 tests in 195.537s` /
`FAILED (failures=2, skipped=6)` — solo el centinela.

### CRITICO-1 — cache por `instance` anulaba el discriminador

La cache indexaba solo el UUID. El puente abre una conexión nueva por request,
así que cachear el veredicto entre sockets distintos era fail-open: el
intruso heredaba el PID del registrado y `_note_poll_pid_locked` lo daba por
BOUND.

**Qué hice.** Eliminé `_pid_attr_cache` / `PID_ATTR_RECHECK_S`.
`resolve_poll_pid` mira **ese** socket cada vez (`_lookup_connected_pid` →
`_connected_server_pid` sobre `getsockname`/`getpeername`). El lookup usa
`psutil.net_connections(kind="tcp")` (filtro de familia, no cache de
veredicto).

**Medida** (esta caja, 2026-08-19, 504 entradas TCP):

- `psutil.net_connections(kind="tcp")`: **2.03 ms/llamada** (media de 20).
- Recorrido sintético de 401 filas (400 TIME_WAIT + 1 ESTABLISHED) ya en
  memoria: **21 µs/lookup**.
- A 5 Hz × 2 peers ≈ 10 lookups/s → ~20 ms/s de CPU. Aceptable frente a
  reabrir BUG-096. Una cache por `(instance, peername)` fallaría casi siempre
  (conexión nueva por request) y no ahorraría el syscall.

**Test:** `test_resolve_poll_pid_does_not_reuse_cached_pid_across_sockets`,
`test_second_socket_same_instance_does_not_receive_mutation`.

### CRITICO-2 — la suite no pisaba `resolve_poll_pid`

`install_bound_peer` escribía `_forced_poll_pids` y cortocircuitaba antes del
lookup. Los tests inyectaban `source_pid=`.

**Qué hice.** Tests de producción: `prepare`+`confirm` (sin
`install_bound_peer`) + `_connections_fn` + dos `_FakeSock`.
`_forced_poll_pids` **sigue** en `install_bound_peer` para los fixtures HTTP
(un solo proceso simulado; no prueban atribución). El fence de atribución se
cubre solo por el camino prepare/confirm.

**Test:** los dos de `ProductionAttributionFenceTest` (rojos en
`red_first_r3.log` antes del arreglo).

### MAYOR-1 — `creation_time_utc` se guardaba y no se comparaba

**Qué hice.** `record_poll(..., source_creation_time=)`. Si no viene, se
consulta `lifecycle.guard.snapshot(pid)` o `_creation_time_fn`.
`_note_poll_pid_locked`: PID distinto **o** `creation_time_utc` distinta →
AMBIGUOUS. Sin PID en el binding, no hay BOUND.

**Test:** `test_creation_time_mismatch_marks_ambiguous`.

### MAYOR-2 — `ready=true` con mutaciones 409

**Qué hice.** `_peer_is_live` solo trata como vivo un `BOUND` (liveness =
`bound_last_poll_age_s`). Cualquier otra clase de fence no es live.
`compute_bridge_ready` corta con la razón del fence (`binding_retired`,
`instance_unknown`, etc.) antes de declarar `ready`. Hint de
`binding_retired`: ya no dice que reenviar apuntará a una instancia nueva;
dice que hay que relanzar el rol y que reenviar seguirá en 409 hasta un
BOUND nuevo.

**Test:** `test_ready_false_when_binding_retired`.

### Menores

1. **Fuga.** `_retire_instance_locked` saca el binding de `_bindings` /
   `_bound_queues` / `_forced_poll_pids` y guarda el UUID en
   `_retired_instances` (clasificar `binding_retired` sin retener colas).
   Test: `test_retire_does_not_accumulate_bindings`.
2. **STARTING y lecturas.** `_enqueue_fence_target` ya no encola lecturas
   en STARTING; 409 `binding_not_ready`. Test:
   `test_starting_does_not_enqueue_reads`.
3. **confirm sin PID.** `confirm` solo pasa a BOUND si el PID es usable.
   `_note_poll_pid_locked` no da BOUND si `binding.pid is None`. Test:
   `test_confirm_without_pid_stays_starting`.
4. **Nombre.**
   `test_internal_vehicle_release_survives_reconnect_gap_on_bound_queue`.
5. **vehicle_release al soltar lease (NO cambiado).** `cleanup_owner` encola
   `vehicle_release` `internal=True` por el fence. Si el client no está
   exactamente BOUND → 409 y `cleanup_degraded: ["vehicle_release_failed"]`.
   Fail-closed y visible: jugador puede quedar atrapado en el vehículo
   hasta que haya un client BOUND. Modo de fallo **nuevo** respecto del
   mundo por rol. Garantía de “se intentó liberar”: el campo
   `cleanup_degraded`. No se relaja el fence.
6. **Dos BOUND del mismo peer.** Código `instance_peer_collision` + hint
   propio (no `legacy_unbound`). Test:
   `test_two_bound_peers_is_instance_peer_collision`.

### `_forced_poll_pids`

Sigue existiendo. Solo lo escribe `install_bound_peer` (fixtures HTTP de un
peer). El camino de producción (`prepare`/`confirm`/`Handler`) no lo toca.
Los tests de CRITICO-2 no lo usan.

## 9. Ronda 4 — re-revisión (suministro, ready, tope, tabla, contadores)

`red_first_r4.log`: 15 tests (14 nuevos de `Round4FenceRegressionTest` +
`test_retire_does_not_accumulate_bindings` extendido).
`FAILED (failures=9, errors=2)` contra el código pre-arreglo.

Cuatro tests ya verdes en el red-first (caracterizan código que ya existía;
no son el arreglo):

- `test_lookup_creation_time_via_lifecycle_like_daemon` — (b) el suministro
  **ya devolvía** un stamp no vacío para un PID vivo. Medido **antes** de
  pasar a fail-closed.
- `test_confirm_and_lookup_share_creation_time_format` — confirm y lookup,
  ambos vía `NativeProcessGuard.snapshot`, ya coincidían.
- `test_never_polled_stays_no_run_not_legacy_unbound` — `no_run` con
  `last_poll_age_s is None`.
- `test_record_poll_looks_up_creation_time_once` — caracteriza que
  `record_poll` llama al lookup una vez. **No acredita NUEVO-2.** El arreglo
  de NUEVO-2 lo acredita `test_handle_poll_does_not_lookup_creation_time`
  (rojo en r4).

`green_r4.log`: `Ran 1667 tests in 200.348s` /
`FAILED (failures=2, skipped=6)` — solo el centinela task9. Base 1653 + 14
tests nuevos. Enforce `.c` no se tocó (hashes idénticos a ronda 3).
`native_process_guard.py` **no** se modificó: está en el cierre del launcher
nativo (`app_module_drift`).

### MAYOR-1 — política fail-closed del suministro

**(a) Política.** `creation_time` ausente **no acredita BOUND**. Justificación
medida, no de diseño:

1. El suministro de producción **funciona** para un PID vivo. Test (b):
   `state.lifecycle = SimpleNamespace(guard=NativeProcessGuard())` (el mismo
   atributo que asigna `daemon.py:578`), `_lookup_creation_time(os.getpid())`
   == `guard.snapshot(pid)["creation_time_utc"]`, `identity_complete is True`,
   stamp `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Medido en esta caja, PID 5764,
   `2026-08-19T00:32:57.908561Z`.
2. Los tres caminos que devolvían `None` (lifecycle ausente, reader que
   lanza, reader que devuelve None) **entregaban** la mutación al mismo PID
   reutilizado — medido por el revisor y reproducido en rojo.
3. Un fallo legítimo de snapshot (proceso ido, AccessDenied) **no** debe
   dejar el binding acreditado: es el mismo PID con otra identidad. Fail-open
   ahí es exactamente el agujero.

Por tanto fail-closed **después** de (b). `_note_poll_pid_locked:1645-1647`
normaliza ambos lados con `normalize_creation_time_utc`; `None` o distinto →
`identity_mismatch` → AMBIGUOUS, cero comandos. `confirm:657-659` tampoco
pasa a BOUND sin stamp usable.

**(c) Formato.** Productor (`NativeProcessGuard._snapshot_process`) y
comparación no pueden compartir un símbolo en `native_process_guard.py`
sin romper el cierre nativo. El normalizador vive en `instance_fence.py`
(`format_creation_time_utc` / `normalize_creation_time_utc`). El snapshot
sigue con `isoformat(timespec="microseconds").replace("+00:00", "Z")` (el
test lo cita en el source). La comparación re-formatea, así que `Z` vs
`+00:00` no deja AMBIGUOUS permanente.

`install_bound_peer` rellena `_forced_creation_times[pid]` (análogo a
`_forced_poll_pids`). Solo fixtures HTTP. `prepare`/`confirm`/`Handler` no
lo tocan. Los tests fail-closed usan prepare+confirm **sin** ese mapa.

**Tests:** `test_lookup_creation_time_via_lifecycle_like_daemon` (b),
`test_missing_creation_time_without_lifecycle_does_not_deliver`,
`test_missing_creation_time_when_reader_raises_does_not_deliver`,
`test_missing_creation_time_when_reader_returns_none_does_not_deliver`,
`test_creation_time_format_helper_locks_isoformat_microseconds_z`,
`test_confirm_and_lookup_share_creation_time_format`.

### MAYOR-2 — `LEGACY_UNBOUND` ya no es `ready: true`

`_peer_is_live` deja de tratar `LEGACY_UNBOUND` como vivo por
`last_poll_age_s`. `compute_bridge_ready` corta con razón `legacy_unbound`
**solo si** ese peer ha sondeado (`last_poll_age_s is not None`), para no
confundir con `no_run`. Añadida a `READY_REASONS`. Un juego ajeno sondeando
sin `inst=` da `ready:false` / `reason=legacy_unbound` (accionable: relanzar
vía `dayz_test_run`) y mutación 409 `legacy_unbound`. Cero polls sigue
siendo `no_run`.

**Tests:** `test_legacy_unbound_is_not_ready_with_own_reason`,
`test_never_polled_stays_no_run_not_legacy_unbound`.

### MENOR-1 — tope de `_retired_instances`

`OrderedDict`, `RETIRED_INSTANCE_LIMIT = 64`, descarte del más viejo.
`test_retire_does_not_accumulate_bindings` ahora hace 200 ciclos y afirma
sobre `_retired_instances` (no solo `_bindings` / `_bound_queues`).

### NUEVO-1 — medida cargada y cache de TABLA (no de veredicto)

**(a) Medida**, esta caja, 2026-08-19.

En reposo (sin inflar):

| | |
|---|---|
| tabla TCP | 766 (TIME_WAIT 539, ESTABLISHED 159, LISTEN 58) |
| loopback | 570 (TIME_WAIT 500) |
| `net_connections(kind='tcp')` | min 2.768 / **mediana 2.893** / **p95 4.498** / max 5.667 ms (n=40) |

Régimen cargado (800 connect/close loopback para engordar TIME_WAIT, el
mismo mecanismo que el puente):

| | |
|---|---|
| tabla TCP | **2429** (TIME_WAIT 2227, ESTABLISHED 135, LISTEN 58) |
| loopback | 2281 (TIME_WAIT 2212) |
| lookup | min 9.115 / **mediana 10.785** / **p95 12.980** / max 13.777 ms (n=40) |
| a 10 polls/s | mediana **108 ms/s** de CPU, p95 **130 ms/s** |

El coste escala con la tabla; la tabla la engorda el propio daemon
(conexión nueva por request → TIME_WAIT ~120 s en Windows). No es
aceptable en el hilo del handler. No se reintroduce cache de veredicto.

**(b) Cache de la tabla 50 ms** (`TCP_TABLE_CACHE_TTL_S`). Cada socket se
resuelve contra esa foto. Dos sockets distintos contra la misma foto →
PIDs distintos. Foto vieja que no contiene al intruso →
`instance_unattributed` (fail-closed). TTL 50 ms coalese ráfagas; un poll
aislado a 5 Hz sigue pagando el syscall. No se cachea el PID.

**(c) NUEVO-2.** `_handle_poll` ya no llama `_lookup_creation_time`.
`record_poll` es el único lookup. Lo acredita
`test_handle_poll_does_not_lookup_creation_time` (rojo en r4).
`test_record_poll_looks_up_creation_time_once` no acredita este arreglo.

**(d) NUEVO-3.** Anotado en `_lookup_connected_pid` y en la rama
`instance_unattributed`. No se arregla. Fail-closed de un tick.

**Tests:** `test_tcp_table_snapshot_resolves_two_sockets_to_distinct_pids`,
`test_stale_tcp_snapshot_missing_socket_is_unattributed`,
`test_handle_poll_does_not_lookup_creation_time`.

### Contadores (consejo 18-08)

En `ServerState`, bajo el lock de enqueue/poll, expuestos en
`status_snapshot["fence"]` y reenviados por `core.build_status` a `/status`:

1. `mutation_rejects_by_code` — los ocho códigos pedidos (enteros; extras
   si aparecen).
2. `unaccredited_mutation_deliveries` — canario permanente; debe ser 0.
3. `unaccredited_polls_by_class` — por `bind_label` (p.ej. `LEGACY_UNBOUND`).

Sin UUID entero. **Tests:** `test_status_snapshot_exposes_fence_counters`,
`test_accredited_mutation_does_not_increment_unaccredited_delivery`.

### SHA-256 ronda 4 (solo lo tocado ahora)

Relativos a `C:\Users\guill\AppData\Local\Temp\mcp-laneFENCE-20260818\DayZ Projects\`.

| sha256 | bytes | path |
|---|---|---|
| 54D928A8EF52B9142E678DD4E6A8DCE5EE7C54303DA4B1341F0DEE4C0A624DB6 | 116817 | `DayZ_MCP_dev\tools\dayz_mcp\loopback.py` |
| 448779149530C53D674CF3E29CAF65B40946F037C18A7DF721CF8A380B23D8D6 | 131564 | `DayZ_MCP_dev\tools\dayz_mcp\server.py` |
| 5D2E4818C67FA9961674602B1439EBB4785392377F9546C5339B56A36B2673AE | 5695 | `DayZ_MCP_dev\tools\dayz_mcp\core.py` |
| 65ABF5885FF3CA807735FEDAF39502BE760A761E8584B2A65707CB6293E0C778 | 5421 | `DayZ_MCP_dev\tools\dayz_mcp\instance_fence.py` |
| B881124AEE9869EC37190B908C66EB0DDBE615A1D96A872C7FADA11F290F044A | 47197 | `DayZ_MCP_dev\tools\tests\test_instance_fence.py` |

No tocados: los tres `.c` (hashes ronda 3), `native_process_guard.py`,
venv, versión `"8"`, spec §9.

### Qué no verifiqué

- Tasa real de `instance_unattributed` in-game (NUEVO-3). Solo anotado.
- Tabla TCP de un daemon+DayZ vivo (prohibido lanzarlos). La medida
  cargada infla TIME_WAIT con connect/close loopback, no con el puente.
- Que `format_creation_time_utc` sea el símbolo que ejecuta
  `_snapshot_process` (no lo es; el productor sigue inline. La igualdad
  se cubre con el test de source + el de confirm/lookup sobre un PID vivo).

## 10. Ronda 5 — la foto de 50 ms no acertaba nunca

`red_first_r5.log`: 6 tests nuevos (`Round5FenceRegressionTest`).
`FAILED (failures=7)` (el de extremos cuenta 2 subtests). **Ninguno nació
verde.**

`green_r5.log`: `Ran 1673 tests in 255.435s` /
`FAILED (failures=2, skipped=6)` — solo el centinela task9. Base 1667 + 6.

### Decisión de la cache (dato primero)

Escenario: 2 peers a 5 Hz, conexión **nueva por request**, client 20 ms
detrás, 30 s equivalentes (150 ticks). Reloj inyectado + tabla fake que
solo contiene la conexión **de ese** poll (el puente cierra al responder).
`TCP_TABLE_CACHE_TTL_S=0.05` contra el `_tcp_connections` / `resolve_poll_pid`
reales.

| | con cache 50 ms | sin cache (ttl=0) |
|---|---|---|
| polls | 300 | 300 |
| TTL hits (foto reutilizada) | 150 (50 %) | 0 |
| **hits útiles** (foto resuelve ESTE socket) | **0** | — |
| hits inútiles (foto y 4-tupla no casa) | 150 (todo el client) | 0 |
| escaneos | 150 | 300 |
| unresolved | 150 (client 150/150) | 0 |

Tasa de acierto útil: **0.0**. El 50 % de “hits” de TTL son exactamente
NUEVO-A: el segundo peer cobra una foto que no puede contener su conexión
nueva. A sondea en t=0 (scan, foto con A). B en t=20 (hit, foto sin B) →
`unattributed`. A en t=200 con conexión **nueva** (TTL caducado → scan).
En régimen estacionario la cache no ahorra un solo escaneo que luego
resuelva; solo ahorra el escaneo que hace falta.

Coste: ronda 4, tabla cargada 2429, mediana 10,785 ms / p95 12,98 ms.
Sin cache: 10 lookups/s → ~108 ms/s mediana. Con cache+reescaneo-al-fallar
serían los mismos 300 escaneos (150 + 150 rescans) y el mismo coste, más
los dos bugs. **Se quita la cache.** Se vuelve al escaneo directo de la
ronda 3. Deuda conocida: ~10,8 ms/poll con tabla cargada. La salida real
es keep-alive en el puente (Enforce, fuera de v1): encogería TIME_WAIT y
el número de lookups a la vez.

Single-flight (NUEVO-D) **no** se añade: esperar la foto del otro handler
es el mismo fallo (la foto del primero no tiene la conexión del segundo).

**Tests de aceptación:** (f)
`test_phased_same_run_client_never_unattributed` (0/40
`instance_unattributed` en el client); (d)
`test_phased_intruder_marks_ambiguous` (BINDING acaba AMBIGUOUS).

### Residuos MAYOR-1

**(a) Diagnóstico.** Fallo de *lectura* del stamp ya no es AMBIGUOUS.
Clase `creation_time_unreadable` (estado, 409, `READY_REASONS`, hint propio:
no hay segundo DayZ). PID distinto sigue siendo AMBIGUOUS aunque el stamp
no se lea. Fail-closed intacto: cero comandos, enqueue 409.

**Test:** `test_unread_creation_time_has_own_class_not_ambiguous`.

**(b) Normalizador.** `normalize_creation_time_utc` captura
`OSError`/`OverflowError`/`ValueError`/`TypeError` y devuelve `None`.
Nunca lanza dentro de `_note_poll_pid_locked` (bajo `self._lock` → 500
en `/poll` = backoff del puente, §2.7).

**Test:** `test_normalize_creation_time_utc_rejects_extremes_without_raising`
(`9999-12-31T23:59:59.999999Z`, `1601-01-01T00:00:00.000000Z`).

### Menores

1. Contador: `unaccredited_mutation_enqueues` (se incrementa al encolar,
   no al entregar). Sigue debiendo ser 0. Test:
   `test_status_snapshot_names_unaccredited_enqueues_not_deliveries`.
2. Atajos: `_forced_poll_pids` y `_forced_creation_times` desaparecen.
   Un solo objeto `TestIdentityOverride` en `_test_identity_override`
   (`None` en producción). Solo lo escribe `install_bound_peer`. Test:
   `test_test_identity_override_is_the_only_shortcut`.

### Corrección de atribución ronda 4

NUEVO-2 lo acredita `test_handle_poll_does_not_lookup_creation_time`
(rojo en r4). `test_record_poll_looks_up_creation_time_once` es
caracterización de `record_poll`, no de ese arreglo.

### SHA-256 ronda 5

Relativos a `C:\Users\guill\AppData\Local\Temp\mcp-laneFENCE-20260818\DayZ Projects\`.

| sha256 | bytes | path |
|---|---|---|
| 159B133E9080983D00020909A2EE893AE45437FF6B42AADBDD6D8F34111E2A6F | 118883 | `DayZ_MCP_dev\tools\dayz_mcp\loopback.py` |
| 56AEF2F052608C8B7639A74D0E8B27558C543DCCC6536CC5F2E8005B26AF69ED | 131688 | `DayZ_MCP_dev\tools\dayz_mcp\server.py` |
| B1D5E303F9FD5FD485C9C53CEBFFB3BA7940C18505C2A1871A8D8425677380E1 | 5886 | `DayZ_MCP_dev\tools\dayz_mcp\instance_fence.py` |
| FCF47CBA44492E50529B755BBC85F1EDD72AC072621393A7A67727B654675D86 | 54382 | `DayZ_MCP_dev\tools\tests\test_instance_fence.py` |

`.c` idénticos a ronda 2/3. `core.py` y `native_process_guard.py` no se tocaron.

### Qué no verifiqué

- 30 s con sockets TCP reales y `psutil.net_connections` (la medida es el
  modelo de conexión-nueva-por-request contra el código de cache; el
  mecanismo no depende de psutil).
- Coste in-game del escaneo directo (DayZ no se lanzó). Queda la mediana
  10,8 ms de la ronda 4 con tabla cargada.

## 11. Ronda 6 — todo EXITED retira bindings

`red_first_r6.log`: 5 tests, `FAILED (failures=5)`. Ninguno nació verde.

`green_r6.log`: `Ran 1678 tests in 263.889s` /
`FAILED (failures=2, skipped=6)` — solo el centinela. Base 1673 + 5.

### Los cuatro sitios (después de persistir EXITED, mismo lock, payload intacto)

| función | motivo | test |
|---|---|---|
| `begin_release_owner.cleanup` | `released` | `test_release_owner_retires_bindings` |
| `repair_recovery_fault` | `recovery_repaired` | `test_repair_recovery_fault_retires_bindings` |
| `repair_manifest_recovery` | `manifest_repaired` | `test_repair_manifest_recovery_retires_bindings` |
| `admin_reconcile` | `admin_reconciled` (solo si `state == "EXITED"`; con supervivientes queda `RUNNING_IDLE` y el binding sigue) | `test_admin_reconcile_retires_bindings` |

No se tocó `compute_bridge_ready` ni `_enqueue_fence_target`. No apareció un quinto camino que sobreviviera a las cuatro llamadas: el AST no encontró más `state = "EXITED"` huérfanos.

Ya retiraban: `stop_run` (`stopped`), `_reap_run_locked` (`reaped`).

### Test de invariante

`test_every_exited_path_retires_bindings` recorre el AST de
`process_lifecycle.py`. Toda función (incluidas anidadas, p.ej.
`begin_release_owner.cleanup`) que asigna `.state = "EXITED"` (constante o
`IfExp`) debe llamar `_retire_run_bindings`.

**Excepciones declaradas:**

- `_settle_failed_launch`: EXITED solo si `previous is None`; `start_run`
  ya llamó `_retire_minted` en `:1372` / `:1388` / `:1405`.

Si mañana hay un quinto camino de salida, este test se pone rojo.

### SHA-256 ronda 6

Relativos a `C:\Users\guill\AppData\Local\Temp\mcp-laneFENCE-20260818\DayZ Projects\`.

| sha256 | bytes | path |
|---|---|---|
| B27E0CE5A35E28FD6BEA207A0ACA0800FD4BB45123185C863A3524DAFA903E15 | 106734 | `DayZ_MCP_dev\tools\dayz_mcp\process_lifecycle.py` |
| 1306A55EEA0E69A5976514F1EB0A718B201FDD7F1BE3F3021E87F4A93D4922EA | 61521 | `DayZ_MCP_dev\tools\tests\test_instance_fence.py` |

`.c` idénticos a ronda 2. No se tocó loopback/server/ready.

### Qué no verifiqué

- Un admin_reconcile con `empty=True` (sin procesos) también pone EXITED
  y ahora retira; el test usa el camino con PID gone, no el empty.
- No hay quinto camino de EXITED en este fichero; no busqué `state = 'EXITED'`
  en otros módulos.

