# product-spec.md — DayZ-MCP

> **Definición de Producto Final (DPF)** — el contrato de "qué es terminado".
> Lo lee toda sesión al entrar (referenciado desde `CLAUDE.md`). Es el target estable;
> el progreso vivo va en `HANDOFF.md` y handoffs (`30_Sessions/`), NO aquí.
> Se cierra/actualiza con el Grill de producto (workflow.md §"Grill — Modo A").
> Diseño completo en [`dayz-mcp-architecture.md`](dayz-mcp-architecture.md).

## Qué es "terminado" (alcance acordado)

Un **servidor MCP** que expone DayZ (DayZDiag) como **tools tipadas** para que un agente
**conduzca el juego y extraiga datos estructurados**, server-authoritative, **sin teclas SO
ni OCR**. "Terminado" = la **tool surface completa** (11 tools / 6 dominios en el alcance
original, ver `dayz-mcp-architecture.md` §4; hoy son 54 tools registradas, ver
`README.md`) operativa vía MCP stdio, con **seguridad fail-closed**, y
captura visual por **window-grab** del cliente renderizado. Se entrega por las 5 fases del
§8 del architecture doc (POC → Control → Observación → Visual → MCP completo).

**Confirmado con usuario 2026-06-06** (Grill Modo A; ver Changelog).

## Cláusula de desafío (el criterio sirve a la intención)

Cada criterio de abajo es la forma *verificable* de una **intención** (`Intent` de su grupo).
El criterio binario es el gate y **no se cumple a la letra rompiendo el espíritu**. Si al
planificar/implementar una fase se detecta que (a) un criterio **choca** con su `Intent`,
(b) hay un camino **más simple** que satisface el `Intent` igual, o (c) el `Intent` revela un
**criterio que falta**, hay que **surfacearlo en el Grill de plan (Modo B)** ANTES de
implementar. El usuario adjudica; todo cambio baja al *Changelog de alcance*.

## Criterios de aceptación verificables

> Estado: ✓ hecho · ⏳ en curso · ❓ sin empezar · [verify] correcto offline, falta validación in-game.

### A — Transporte & readiness (POC fase 0)

> **Intent**: probar que un agente puede mandar un comando y recibir **datos estructurados**
> de DayZ **sin estancar el sim de 60Hz**. Es el cimiento de todo el resto: si el round-trip
> async no funciona o bloquea el tick, ninguna otra tool es viable.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| A1 | `query_player_state` devuelve la **posición autoritativa** del player | pos devuelta == coord del **spawn determinista de la mission de test** (no la tool `world_spawn`; la mission *setea* la coord, el POC la *lee*), error < 0.5 m | ✓ **in-game 2026-06-07 (re-cert X.5)** — bridge vs marker independiente = **0.0313 m** < 0.5 m (`target`=marker, fallback a `playerReadyPos` eliminado) |
| A2 | El round-trip **no bloquea el tick** `[intent: el RTT no debe congelar el sim]` | el tick avanza durante un GET async en vuelo: `tick_poll_callback − tick_poll_sent` ≥ 5 con `/poll` retrasado por el server | ✓ **in-game 2026-06-07** (ticks_in_flight=4741 con delay 600ms; run_045213) |
| A3 | **Correlation-id** casa pedido↔resultado | 2 comandos encolados resuelven cada uno a su propio resultado, sin cruce | ✓ **in-game 2026-06-07** (ids 9,10, pos_distance 0.0) |
| A4 | **Fail-closed** | request sin key → 401; key incorrecta → 401; comando fuera de whitelist → rechazado; si el servidor no puede confirmar bind 127.0.0.1 → aborta al arrancar | ✓ **in-game 2026-06-07** (sin key→401, key mala→401, `/enqueue` sin key→401, `evil`→400) |
| A5 | **Resiliencia** del lazo | servidor Python caído: el mod no crashea ni spamea (reintenta en cadencia con backoff); al volver el servidor, el round-trip se reanuda solo | ✓ **in-game 2026-06-07** (server caído 6s: backoff `error=7` 1→2, sin crash; post-relaunch `ok=1`) |

### B — Control (fase 1)

> **Intent**: montar un escenario y **conducir sin input del SO**.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| B1 | `world_spawn` crea la entidad pedida en la pos pedida | entidad existe server-side post-spawn (enumeración / raycast) | ✓ **in-game 2026-06-08** (id=22 `Hatchback_02` found=1, pos_real verificado; + negativos unknown_type/bad_pos×2/bad_flags/args-no-dict→400) |
| B2 | `vehicle_enter` sienta al player | `GetCommand_Vehicle()!=null` + `!IsGettingIn()` + `GetVehicleSeat()==DayZPlayerConstants.VEHICLESEAT_DRIVER` (constante nombrada, NO literal 0) + 1-2 ticks | ✓ **in-game 2026-06-08** (id=27 seated=1 seat="driver"; requirió fix de deadlines tick→tiempo, anim ~2 s) |
| B3 | `vehicle_drive` mueve el vehículo | PROBE de decisión completado **in-game 2026-06-08**: motor controlable server-side (`engine_on_server=1`) pero **movimiento client-authoritative** (speedo≈0 / pos_delta≈0 con throttle 1.0, fixture_ready=true, net=PHYSICS — confirma `actionstartengine.c:51-58`). Conducir desde el server **descartado**. | ✓ probe (client-auth); **conducción DIFERIDA a fase client-peer** → retomada por el **grupo G** (fase 5, peer owner) |

### C — Observación sin píxeles (fase 2)

> **Intent**: emitir **verdicts de test headless** sin necesitar captura visual.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| C1 | `scene_raycast` devuelve hit estructurado | objeto/dist/normal vs un objeto a distancia conocida | ✓ **in-game 2026-06-08** (run PBO, `C:\tmp\fase2-verdict.json` overall_pass): smoke al suelo, din `Hatchback_02` + estático `Land_Misc_Well_Pump_Blue` (server golpea geo estática), negativo al cielo `hit=0`. **Primaria=`RayCastBullet`** (normal unitaria; el `dir` de `RaycastRVProxy` no lo es). Sin `GetCrosshairObject` (from/to explícitos) |
| C2 | `telemetry_read` devuelve datos del fixture | parse correcto de un fixture/JSON-lines conocido | ✓ **in-game 2026-06-08** (run PBO): `object_at` (Hatchback `health01=1.0`, pos/orient/vel) + `fixture_jsonl` (`fx2`, value 7.5, 2 líneas) + 5 negativos (bad_args/fixture_not_found/parse_error/type-not-found/ambiguous) |

### D — Visual (fase 3)

> **Intent**: "ver" multi-ángulo para verdicts que **requieren píxeles**. Exige cliente con renderer.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| D1 | `camera_set`/`camera_get` posan y miden la cámara | **`Camera.GetCurrentCamera().GetTransform`** == pose comandada (tolerancia), conserva roll (set vía `SetOrientation` yaw/pitch/roll; NO el indexado `GetCamera(0,…)`, solo GAME_TEMPLATE) | ✓ **in-game 2026-06-10** (`camera_set`/`camera_get`: matrix==pose `matrix_max_error 3.4e-05`, roll preservado, pos 3.9e-05 m, fov exacto; fail-closed cliente OK; GATE=PASS) |
| D2 | `capture_screenshot` entrega la imagen por window-grab (**síncrono, sin job-id, acotado a un run en estado vivo**) | imagen no-negra con contenido real, **JPEG q82 por defecto** (PNG seleccionable, `DEFAULT_FORMAT` en `mcp_capture.py`), **≤ ~25k tokens (Claude Code; NO 1MB)** vía downscale agresivo, y **best-of-N host-side** por frame-diff (`choose_stable_frame` elige el frame de menor delta adyacente: es **selección, no gate**). Lo que SÍ rechaza `grab_stable_frame`: captura fallida, `clientStats` no verificables (`frame_client_area_unverified`) y client-area todo-negro (`frame_client_all_black`); la inestabilidad **no** es motivo de rechazo. El gate de estado es de lifecycle (`_CAPTURE_LIVE_RUN_STATES`), no de readiness in-world | ✓ **in-game 2026-06-10** (host-side: selector `class=='DayZ'`+DPI-aware, best-of-N, downscale a presupuesto, ImageContent síncrono; frame real meanB 155 / nonBlack 0.9999, ≤ tokens; GATE=PASS) |

### E — MCP completo & seguridad endurecida (fase 4)

> **Intent**: el end-goal **usable e instalable**, con la seguridad llevada a producción-local.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| E1 | Tool surface completa vía **MCP stdio** | las **12 tools canónicas** (lista en `plans/2026-06-10-fase4-mcp.md` §2; con install default `tools/list` muestra 11 — `exec_enforce` solo con flag ON) aparecen y responden desde un cliente MCP real | ✓ **in-game 2026-06-10 (gate 4A+4B)** — 9 tools 4A (gate 4A) + `world_time_set`/`world_weather_set`/`exec_enforce` (gate 4B: tools/list=12 con flag, applied read-after-write, rangos fail-closed). Caveat: la EJECUCIÓN de `exec_enforce` es limitación de engine (ver Fuera de alcance) |
| E2 | Seguridad endurecida | **handshake de versión bridge+juego** validado fail-closed (`ver=` en `/poll`; sin snapshot ERPCs — el transporte T-A no usa RPCs); `exec_enforce` OFF-default + allowlist exacta + audit log (breakglass auditado) | ✓ **in-game 2026-06-10 (gate 4B)** — handshake `version_state` 4 casos (ok / bridge-version mala / game-version mala / legacy_blocked) fail-closed; key/whitelist/bind ✓ (A4); `exec_enforce` gating exacto + audit JSONL + OFF-default ✓ (la ejecución del script es limitación de engine) |
| E3 | Packaging / install | instala y arranca con un comando documentado (host-path test, no rutas de sandbox) | ✓ **in-game 2026-06-10 (gate 4A)** — `install-mcp.ps1` end-to-end en venv limpio (Pillow added to requirements) + registro `claude mcp add` real |
| E4 | Concurrencia | lock de instancia (bind exclusivo del puerto loopback) + mutex global de tool-calls (el SDK MCP no serializa): dos tool-calls no corrompen estado/cámara | ✓ **in-game 2026-06-10 (gate 4A)** — lock exclusivo verificado con 2ª instancia real (`allow_reuse_address` left False on Windows) + 2 `camera_set` paralelas serializadas sin corrupción |

### F — Broker / multi-sesión (refactor 2026-06-23)

> **Intent**: que VARIAS sesiones Cowork tengan las tools `dayz-mcp` cargadas y operativas a la
> vez sobre UN único juego, sin que la 2ª sesión se quede sin tools por el lock exclusivo del
> puerto. Un daemon standalone es el único dueño de `:8765` y de la conversación con el juego; las
> sesiones corren en modo cliente y proxyan por HTTP. Reencuadra E4: el lock de instancia pasa a
> ser del DAEMON. Python-only: el juego sigue sondeando un único `:8765`, Enforce/PBO intactos.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| F1 | N sesiones con tools cargadas+respondiendo contra un juego | offline: ≥2 clientes HTTP contra un daemon reciben resultado (`test_client_mode.test_two_clients_one_daemon_both_get_results`) + E2E binario real 2 clientes concurrentes (`_broker/e2e_daemon.py` P3); in-game: usuario | [verify] offline ✓; falta in-vivo (usuario) |
| F2 | Lifecycle del daemon: auto-spawn lazy detached, sobrevive al spawner, idle self-shutdown sin juego ni clientes | E2E binario real: bind+`/status` (P1), idle libera puerto exit 0 (P4), sobrevive a la salida del spawner detached (P5) | [verify] offline ✓ (`_broker/e2e_result.json`); supervivencia bajo el Job-Object de Cowork = gate in-vivo (usuario) |
| F3 | Fail-closed preservado en el split | key/whitelist/version-gate/exec-chokepoint enforced daemon-side (`test_daemon`: `/status` 401 sin key, `/enqueue` 409 `version_blocked`; suites exec/x5 intactas) | ✓ offline |
| F4 | Concurrencia: comandos serializados; conduce una a la vez | el lock `ServerState._lock` serializa cada comando en el daemon; cruce cámara/captura entre sesiones = limitación documentada (Fuera de alcance), sin lease en v1 | ✓ offline |
| F5 | Reclaim no mata un daemon sano; recupera el puerto de un holder no-responsivo | `test_daemon`: `try_reclaim_unresponsive_listener` aborta si `/status` responde sano, reclama solo un dayz_mcp no-responsivo; C1 (embedded orphan) sigue cubierto por `test_port_reclaim` | ✓ offline |

### G — Drivability real & diagnóstico de acceso (fase 5)

> **Intent**: cerrar el bucle de test de coches **conducibles** vía MCP — el agente itera un coche
> (rip → conducible) sin humano en el juego: que se MUEVA (no probe inerte), que se diagnostique por
> qué "Get in" no aparece, con verbos granulares en el peer **owner**. Recoge la conducción que B3
> difirió (B3 probó que server-side no mueve PHYSICS). La orquestación de la escalera vive en la skill
> `dayz-mcp-verify`, NO en el MCP.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| G0 | (Spike S0, de-risk) conducir desde el peer **owner** mueve un coche PHYSICS | `pos_delta>1.0 m` tras 2 s throttle=1.0 (CivilianSedan) **+ reporta `IsOwner()`/`GetOwnerIdentity()`/net id** (evidencia directa de ownership, no solo conductual). STOP si `pos_delta≈0` | ❓ |
| G1 | Verbos de control en el peer **cliente**: `engine_set`, `vehicle_control` (throttle/steer/brake/handbrake **sostenido + bounded/deadman**), `gear_shift`, `vehicle_telemetry`, `vehicle_release` | gate A: acelera/gira/cambia marcha/frena; el held-state se reaplica cada tick; `vehicle_release` **y** un TTL/deadman auto-sueltan; fail-closed por **rango Y NaN/Inf** | ❓ |
| G2 | `query_get_in_condition` (server) reporta el primer gate que bloquea el radial | gate B: coche OK→`available`; fixture con `component` válido + asiento mapeado pero `CrewCanGetThrough=false`→`first_block`; componentNN ausente→`first_block`. **`component` OBLIGATORIO para un veredicto PASS** (sin él, diagnóstico parcial, nunca PASS) | ❓ |
| G3 | `vehicle_trace` owner-client entrega un stream atómico pull, append-only y lease-gated del mismo coche/reloj: 20–60 Hz, ≤8192 muestras, chunks ≤64, control solicitado+aplicado, pose/velocidad/dirección, 4 ruedas, engine/marcha, `IsOwner()`/net id y `OnContact` raw no-wheel; release/expiry/shutdown limpia trace+control; artefacto host determinista con hashes calculados | fixtures adversariales RED→GREEN + suite host; source-contract y bridge v6; PACKONLY; control live `CivilianSedan` ≥2 s y ≥20 Hz efectivos con owner/net id estable, readback ≤0,001 y al menos un contacto corporal observado en owner client; bundle repetido byte-idéntico; ausencia de callback/overflow/gap/cleanup/lifecycle limpio = RED | ❓ |
| G4 | `restore_gameplay` devuelve al jugador local simulación, input y HUD tras control de cámara; `vehicle_get_in_client` aplica el mismo restore idempotente antes del get-in | contratos offline de whitelist/args/peer/tool/dispatch y orden del guard; PACKONLY; gate in-game freecam→restore y freecam→get-in sin `not_seated`. No implica desactivar/borrar la cámara activa | ❓ |

Fuera de G (fase 5): simulación de input crudo / radial UI; la orquestación de la escalera (skill).
Aceptación detallada: gates S0/A/B de `plans/2026-06-28-fase5-drivability-autonoma.md`.

### H — Coordinación segura de sesiones de agentes

> **Intent**: que Claude y Codex compartan una sola caja DayZ sin intercalar secuencias
> mutantes, abandonar ownership ni terminar procesos ajenos. Las lecturas puras no esperan el
> lease; mutaciones y lifecycle usan una cola FIFO con recuperación fail-closed y evidencia auditable.

| # | Criterio | Cómo se verifica | Estado |
|---|----------|------------------|--------|
| H1 | Topología única: todas las sesiones interactivas Claude/Codex usan `--client`; un daemon/listener autoritativo | topología, installer y launchers verificados offline; las configs efectivas de 2 Claude + 2 Codex se ejercitan en H8 | ✓ offline |
| H2 | Identidad y autorización: ticket/lease ligados al cliente; token ajeno, expirado o de otra generación no autoriza | tests HTTP/MCP de suplantación y restart; rechazo antes de `/poll` y sin side effects | ✓ offline |
| H3 | Lecturas puras admitidas sin lease; mutaciones y comandos desconocidos requieren lease activo | A mantiene lease, B lee; mutación de B devuelve `lease_required`; comando nuevo cae en mutating-default | ✓ offline |
| H4 | Cola FIFO estricta, `acquire` idempotente, TTL exacto 120 s y promoción sólo con `session_wait` vivo | A→B→C, ticket duplicado no duplica posición, reloj inyectable 119/120/121 s; release/expiry sin `session_wait` vivo no conceden leases | ✓ offline |
| H5 | Release/expiry cancelan comandos no entregados, intentan `vehicle_release` solo sin cuarentena retail, no matan DayZ/daemon y no reviven comandos al reconectar | tests de cleanup owner-scoped + reconnect; bajo cuarentena omiten la mutación y auditan `cleanup_degraded=retail_quarantine`; caída del stdio owner concede al siguiente tras TTL; restart invalida leases/tickets previos | ✓ offline |
| H6 | Lifecycle solo opera sobre runs registrados y revalida PID + creation time + executable/command-line fingerprint | mismo mod en dos agentes no confunde ownership; PID reutilizado, foreign, legacy o identidad incompleta quedan intactos | ✓ offline |
| H7 | Auditoría y protocolo durable: JSONL sin secretos, doctor y cierre verificable | reconstruir acquire→wait→grant→use→release/expiry; negativos de key/token; fixture E2E final con `own_lease=none`, `own_ticket=none`, `pending_commands=0`; el `session_status` real se exige en H8 | ✓ offline |
| H8 | Gate combinado real sobre una caja y un juego | 2 Claude + 2 Codex: lecturas paralelas, mutaciones FIFO, release, expiry por caída, adopción/reemplazo seguro y estado final limpio | ✓ in-game — sustitución 4-Codex aprobada |
| H9 | Adquisición en espera request-bound y liveness de cola | `session_acquire_wait` request-bound nunca devuelve `queued`; timeout/cancel no deja ticket/lease oculto; sólo `session_wait` vivo promueve cabeza; release/expiry no hacen grant ciego; launcher nativo/neutral respecto al consumidor, registrado (path+SHA, sin identidad/token), no autoriza PowerShell ni `.ps1`; gate local `fifo_grants_without_live_wait=0`; gate real 2 Claude+2 Codex abierto hasta proveniencia externa | [verify] offline ✓; falta gate real 2 Claude + 2 Codex |
| H10 | Acreditación pre-request del daemon y confidencialidad del control | Todo cliente HTTP autenticado acredita sobre el socket loopback ya conectado un owner PID único y estable, executable/argv/cwd canónicos y dos snapshots nativos v2 idénticos **antes de emitir el primer byte HTTP**, incluido `?key` y cualquier body con identidad/lease; listener foreign, rebind, PID reuse/drift o identidad parcial/ambigua fallan cerrados con `http_bytes_sent=0` y `key_disclosed=0`. Gate de routing sobre ClientRuntime, doctor, admin y lifecycle, más fixtures foreign/rebind | ✓ offline |
| H11 | Ejecución y parada de tests como tools MCP de primera clase | `dayz_test_run` y `dayz_test_stop(run_id)` aparecen como tools tipadas en client mode; la llamada espera FIFO hasta terminar, comunica progreso `validating → queued(position) → executing → finalizing`, reutiliza la transacción nativa H9 y nunca acepta `dev_root`, source, ejecutable, rutas arbitrarias, PID, argv ni lease token. Mission pública se limita a aliases y mods públicos a identificadores relativos de un segmento; defaults internos pueden proceder de la policy sellada. Todo `run_id` de extensión debe estar idle y pertenecer al proyecto seleccionado antes de enqueue; `adopt` cierra la carrera. Run exitoso devuelve `run_id` y queda `RUNNING_IDLE`; stop deriva el proyecto del manifiesto, adquiere lease, adopta y detiene sólo ese run. Resultado compacto y acotado, cancel/fallo sin ticket/lease oculto y cleanup del run nuevo verificados offline; smoke real controlado posterior | ✓ offline + smoke real MERCEDES 2026-07-23; Utopia falla cerrado por crash propio de DayZDiag |
| H12 | Recuperación de credencial obsoleta en clientes MCP vivos | Ante un 401 de un daemon ya acreditado, bridge y control revalidan la misma policy/keyfile/provenance, releen la credencial mediante el lector endurecido y reintentan exactamente una vez el mismo request bajo el deadline original. Un segundo 401, una fuente no acreditada o un fallo de acreditación fallan cerrados con código estable y sin secretos. El mecanismo es común a todas las tools, seguro bajo concurrencia, no inicia/reemplaza daemons por un fallo de autenticación y no crea, adopta, libera ni altera leases, tickets, runs u ownership. Un cambio de `daemon_generation` conserva la invalidación H2/H5 | ✓ offline + E2E multiproceso aislado |

**Excepción de ejecución aprobada 2026-07-16:** mientras no haya créditos Claude, el usuario
autoriza sustituir el reparto 2+2 de H8 por **4 sesiones Codex fresh**. Para acreditar el gate
funcional deben conservar cuatro `session_id` y PID distintos, una misma `daemon_generation`,
la secuencia completa y el cierre limpio. La evidencia se etiqueta `4-Codex`; esta excepción no
convierte en verificación in-game la mitad Claude de H1.

Estado de cierre 2026-07-16: H8 funcional pasó en una ejecución real de 4 sesiones Codex fresh,
con una generación compartida, FIFO/TTL/lifecycle completos y cierre limpio. El doctor final quedó
sin findings y no quedaron procesos DayZ ni UDP 2302. Evidencia:
`reviews/2026-07-16-h8-real-4-codex.json` (SHA256
`E49A4A224EE782C99C86C5D71F8DEE8EB502213B94683D619AF3BF2F26CD873A`). La configuración
efectiva mixta 2 Claude + 2 Codex de H1 sigue sin verificación in-game.

Aceptación detallada: `plans/2026-07-14-agent-session-coordination-design.md` §13.

## Fuera de alcance (explícito)

- **Teclas SO / OCR / inyección de input raw**: el control es engine-native; el único elemento
  externo es el window-grab **pasivo** (lee píxeles, no inyecta input).
- **Build headless-only para la fase visual**: la captura exige un cliente con renderer; una
  build server-only no rinde imagen (sí datos).
- **Multi-instancia concurrente** en fases 0-3 (histórico): era "un DayZDiag = un dueño"; el
  broker (grupo F, 2026-06-23) lo convierte en feature soportada — muchas sesiones, un juego,
  comandos serializados por el daemon. Sigue siendo un único JUEGO (no multi-instancia de DayZ).
- **`exec_enforce` como tool universal**: es breakglass auditado con allowlist, no la vía normal.
- **Exposición a red / multi-usuario**: solo `127.0.0.1`, local single-user. Nada de `0.0.0.0`.
- **Arreglar `MakeScreenshot`**: está roto en el engine (T165276); no es nuestro trabajo — se
  rodea con window-grab.
- **Ejecución funcional de `exec_enforce` en el server headless** (2026-06-10):
  `ExecuteEnforceScript` es proto "Developer only" (`game.c:776`) y devuelve `false` en el
  server diag `NO_GUI` incluso con el wrapper vanilla EXACTO del script-console — limitación de
  engine análoga a `MakeScreenshot`. Lo que la tool SÍ garantiza y está verificado in-game es la
  **salvaguarda de seguridad** del breakglass (gating allowlist-exacto + audit JSONL + OFF-default);
  el efecto del script en el server headless es best-effort, no contractual. No es nuestro trabajo
  hacer funcionar una API Developer-only en contexto headless.

## Referencia de paridad (anatomía del "MCP que controla un juego")

> Aquí el referente no es un vanilla DayZ sino el **prior-art de MCP servers de control de
> juego/3D**. De ahí sale la anatomía de "qué trae de serie un MCP server de este tipo".

- **Referencia**: MCP servers existentes — Blender MCP, Unreal MCP, Unity/Godot MCP (patrones:
  `get_property`/`set_property` genéricos, `status()` antes de mutar, errores de negocio vía
  `isError` y no excepción de protocolo, límite `ImageContent` <1 MB, transporte stdio JSON-RPC).
- **Anatomía extraída**: tool surface tipada por dominio · readiness/job-id para ops asíncronas ·
  status-before-mutate · imagen como `ImageContent` base64 <1MB · seguridad por bind local.
  ⚠️ **Verificación**: este prior-art viene de W2 (subagentes) + web, **NO re-verificado por
  Claude** (pre-output-discipline Pattern 2). Confirmar contra los repos reales antes de tratar
  un patrón como obligatorio.
- **Residuo genuinamente nuevo** (no hay referente DayZ): transporte async **dentro del tick**
  DayZ sin bloquear 60Hz, server-authoritative vía `MissionServer`, captura por **window-grab**
  (porque `MakeScreenshot` está roto). Marcado `[verify in-game]`; es lo que el POC de-risca.
- **Estado de extracción**: patrones recogidos en `dayz-mcp-architecture.md`; APIs DayZ
  re-verificadas por Claude (`dayz-harness-apis.md` + spot-checks de esta sesión).

## Changelog de alcance

- **2026-08-07 (Fase 1 + Fase 2 del plan v2 — DOS cambios de contrato observables):**
  el servidor está publicado a la comunidad, así que ambos se declaran aquí.
  1. **`dayz_test_run` / `dayz_test_stop` ya no emiten el literal exacto
     `dayz_test_failed`**: pasan a `dayz_test_failed:<TipoDeExcepción>` (p. ej.
     `dayz_test_failed:NativeLauncherBackendError`). Se emite el TIPO, nunca el
     mensaje, que puede llevar rutas del host. Un consumidor que compare por
     igualdad exacta se rompe; el contrato soportado pasa a ser
     `startswith("dayz_test_failed")`. Motivo: el `except Exception` tapaba la
     causa y hacía indiagnosticable `build:true` (F1.4, precondición de F5.1).
  2. **Los campos de referencia que un verbo no rellena dejan de viajar**: hasta
     ahora `MCPResult` es una clase plana única, así que todo verbo emitía
     `players:[]`, `raycast:{}`, `telemetry:{}`… aunque no los produjera, y el
     consumidor no podía distinguir «cero jugadores» de «este verbo no informa de
     jugadores» (el problema anotado contra GameMaster el 2026-07-29). Ahora esos
     campos se omiten. **Excepción preservada**: `query_all_players` sigue
     devolviendo `players: []` con cero jugadores — es éxito semántico verificado
     in-game el 2026-07-29. Solo se podan contenedores de referencia vacíos;
     los escalares (`deleted:0`, `found:false`, `seated:false`) nunca se tocan,
     porque un falsy no se distingue de un campo sin asignar (F2.2).

  Añadido además el verbo `logs_since(marker)` — lectura incremental de RPT y
  `script_*.log` del perfil del run activo, sin lease (F2.1) — y el aviso
  `daemon_module_stale` en `bridge_status`, que delata que `loopback.py` o
  `server.py` se editaron después de arrancar el daemon y que por tanto lo que
  corre no es lo que hay en disco (F2.3). Sin PBO, sin tocar Enforce.
  Plan: `plans/2026-08-07-mejoras-y-capacidades-plan-v2.md`.

- **2026-08-17 (dos cambios de contrato observables):**
  1. **`entities` entra en la poda de referencias.** El campo se quedó fuera de
     `PRUNABLE_FIELDS` cuando se añadió `entities_query`, así que hasta hoy TODO
     verbo emitía `entities: []`. Ahora se poda como los demás contenedores de
     referencia vacíos. **Sin excepción semántica**, a diferencia de
     `query_all_players`: `entities_query` informa además de `count_total`, que
     es un entero y por tanto nunca se poda, de modo que un resultado vacío
     sigue diciéndolo. La excepción solo aplica cuando el contenedor es la
     ÚNICA salida del verbo, que es el caso de `players` y no el de `entities`.
  2. **`ui_set_text` acepta `TextWidget`.** Antes devolvía `text_not_writable`
     para una etiqueta plana, pese a que `SetText` existe
     (`1_core\proto\enwidgets.c:195`); quedaba fuera por omisión, no por
     política, ya que `ButtonWidget` —igual de no-legible— sí se aceptaba. Es
     un ensanchamiento: ninguna llamada que antes funcionara cambia. Cubre
     también `MultilineTextWidget`, que deriva de `TextWidget`. Caveat que el
     consumidor debe conocer: escribir una etiqueta cambia lo que se DIBUJA, no
     el estado del juego, y `TextWidget` no tiene getter, así que `ui_tree`
     reporta `text_readable: false` para ella. No condiciones un gate sobre un
     texto que escribió este verbo.

- **2026-07-25 (H12 — credencial viva tras rotación/restart):** aprobado e
  implementado un provider único por `ClientRuntime`. Un 401 de un daemon ya
  acreditado permite revalidar la autoridad original, releer el mismo keyfile y
  repetir una sola vez el mismo request bajo el deadline original. Segundo 401,
  source drift e identidad no acreditada fallan cerrados sin `spawn` ni cambios
  de lease/ticket/run. Telemetría RAM-only sanitizada y doctor distinguen
  recovery de desincronización. Gate: 290 focales verdes (1 skip), shake
  concurrente 25/25 y E2E A→B 3/3 con el mismo proceso MCP stdio. Discover:
  1224 tests, con los mismos 14 failures + 29 errors + 4 skips históricos y
  cero rojo H12.
  Plan/review: `plans/2026-07-25-stale-client-credential-recovery.md` y
  `reviews/2026-07-25-stale-client-credential-implementation.md`.

- **2026-07-25 (G3 — `vehicle_trace` atómico owner-client)**: por autorización explícita del
  usuario se añade el criterio G3 para cerrar la instrumentación que H1–H6 de Mercedes necesita
  antes de tuning. Diseño pull por chunks con command ids independientes, `IsOwner()` como
  requisito (`IsAuthorityOwner()` solo diagnóstico), DTO preinstanciados fuera de hooks,
  `OnContact` copiado a primitivas y cleanup lease-aware. No toca Mercedes ni inicia S2.
  Spec/plan: `plans/2026-07-25-vehicle-trace-feature-spec.md` y
  `plans/2026-07-25-vehicle-trace-atomic-instrumentation.md`.
- **2026-07-22 (H11 — lifecycle de test first-class MCP)**: el usuario aprueba dos tools tipadas,
  `dayz_test_run` y `dayz_test_stop(run_id)`, síncronas/request-bound y montadas sobre H9. No se
  añade daemon de jobs, subprocess CLI, rutas libres ni un segundo protocolo de cola. El lease
  sigue siendo privado del harness y nunca forma parte de argv, request pública, logs o resultado.
- **2026-07-22 (D-18 — acreditación pre-request del daemon)**: nuevo criterio **H10**.
  Todo cliente HTTP autenticado debe acreditar la identidad OS exacta del owner del socket
  conectado antes de emitir key, identidad, lease o cualquier otro byte HTTP. Respuestas
  2xx, schemas plausibles o un PID observado antes de conectar no constituyen proveniencia.
  No cambia payload ni formato persistente; endurece A4/F3/H7 sobre el borde cliente→daemon.
- **2026-07-16 (D-17 — excepción H8 4-Codex)**: por falta de créditos Claude, el usuario aprobó
  cerrar el gate funcional H8 con cuatro sesiones Codex fresh, preservando cuatro identidades/PID,
  una generación, la secuencia completa y el cierre limpio. La evidencia queda etiquetada
  `4-Codex`; H1 no adquiere por ello verificación mixta Claude+Codex.
- **2026-07-15 (D-16 — lifecycle retail fail-closed)**: H3/H5/H6/H8 se concretan sin cambiar su
  intención. Lifecycle gestionado inicial solo para la ruta canónica de DayZDiag; Server sigue
  gated por probe y retail no se inicia desde launchers oficiales. Presencia retail externa o
  snapshot desconocido pone las mutaciones/lifecycle en cuarentena hasta rescan cero; lecturas
  puras y coordinación siguen disponibles. El nombre se usa solo para bloquear, nunca para
  ownership ni terminación. Diseño:
  `plans/2026-07-15-agent-session-coordination-retail-lifecycle-delta.md`.
- **2026-07-14 (Coordinación multiagente — diseño aprobado)**: nuevo grupo **H (H1-H8)**.
  Extiende el broker D-14 con identidad por cliente, lease exclusivo para mutaciones/lifecycle,
  lecturas puras sin lease, FIFO estricta, TTL 120 s, cleanup owner-scoped, manifiesto de procesos,
  auditoría JSONL, doctor y protocolo durable Claude/Codex. No crea un segundo servicio ni promete
  aislamiento de SO frente a un usuario con shell completo. Diseño:
  `plans/2026-07-14-agent-session-coordination-design.md`.
- **2026-06-28 (Fase 5 — grill/R22 del plan, adjudicado con usuario)**: nuevo grupo **G (G0-G2)**
  "drivability real & diagnóstico de acceso" — recoge la conducción que **B3 difirió** (server-side
  no mueve PHYSICS) vía peer **owner/cliente** + verbos granulares (`engine_set`/`vehicle_control`/
  `gear_shift`/`vehicle_telemetry`/`vehicle_release`) + `query_get_in_condition`. Orquestación de la
  escalera = skill `dayz-mcp-verify`, no el MCP. **R22 Codex 2026-06-28**: NEEDS-WORK, 3 P1 + 6 P2
  (F5-001..009), los 9 aceptados → plan v2. F5-001 (cerrar el gate DPF antes de S0) adjudicado con
  el usuario (AskUserQuestion): **añadir grupo G** (vs reabrir B3). Plan:
  `plans/2026-06-28-fase5-drivability-autonoma.md`. Review: `reviews/2026-06-28-plan-review-fase5-codex.md`.
- **2026-06-23 (Broker / multi-sesión — IMPLEMENTADO, offline-verified)**: refactor Python-only a
  modelo daemon/cliente para que varias sesiones Cowork tengan las tools `dayz-mcp` a la vez sobre
  un único juego. Decisiones (AskUserQuestion, confirmadas con usuario): (a) auto-spawn lazy
  detached por la 1ª sesión + discovery por `/status`; (b) cliente por defecto, `--embedded` como
  fallback (bare = embedded → gates 0-4 intactos); (c) first-come serializado, sin lease,
  limitación "conduce una a la vez" documentada; (d) version-gate / exec-chokepoint / lock E4 /
  orphan-guard viven en el DAEMON, el cliente solo proxya; (e) registración pasa a `--client`.
  Nuevo grupo **F (F1-F5)**; **E4 reencuadrado** (lock de instancia = del daemon); se retira la
  exclusión "multi-instancia 0-3". Código: `dayz_mcp/{core,daemon}.py` (nuevos) + `loopback.py`
  (`/status`, `touch_client`, 409 `version_blocked` guard-validador, `status_provider`),
  `orphan_guard.py` (`probe_status_healthy`, `try_reclaim_unresponsive_listener`), `server.py`
  (`ClientRuntime` + modos), `install-mcp.ps1` (`--client`). Gates: suite **113/113** (era 91;
  +22 broker) + **E2E binario real 5/5** (`_broker/e2e_daemon.py`: bind, round-trip, 2 clientes,
  idle-shutdown, supervivencia-spawner). **Pendiente: gate in-vivo** (2 sesiones Cowork reales con
  tools cargadas a la vez; matar la spawner) — usuario. Plan: `plans/2026-06-23-broker-refactor.md`.
  Review R21 del código: pendiente (el usuario decide R22-del-plan o R21-del-código).
- **2026-06-10 (Fase 4 — IMPLEMENTADA + gates 4A/4B PASS in-game)**: 4A (wrapper MCP stdio, Codex + receptor + gate 4A PASS: smoke 9/9, regresión fase3, ingame 8/8) + 4B (3 handlers Enforce + version-report, Codex source-only + receptor + rebuild + gate 4B PASS 11/11). **Criterios E1/E2/E3/E4 → ✓ in-game**. Bugs de producto cazados por los gates y arreglados: **GATE4A-001** (Pillow ausente de requirements), **GATE4A-002** (`allow_reuse_address` anulaba el lock E4 en Windows → `ExclusiveThreadingHTTPServer`), **GATE4B-001** (`result.get("ok") is False` nunca matcheaba el int 0/1 del bridge → errores de negocio se devolvían como éxito; afectaba TODAS las tools), **GATE4B-002** (allowlist con BOM crasheaba el arranque → `utf-8-sig`). **GATE4B-LIM** (decisión con usuario, AskUserQuestion): `exec_enforce` mantiene gating+audit como salvaguarda verificada; su ejecución funcional en server headless es limitación de engine documentada (Fuera de alcance, tipo MakeScreenshot). Trail: `reviews/2026-06-10-fase4a-gate-ingame.md` + `2026-06-10-fase4b-gate-ingame.md`.
- **2026-06-10 (Fase 4 — grill de plan + R22, adjudicado con usuario)**: 7 adjudicaciones del Grill Modo B (G-1..G-7, `plans/2026-06-10-fase4-mcp.md` §1): wrapper = SDK oficial `mcp`/FastMCP (smoke-verificado `mcp==1.27.2` sobre Python 3.14.3 host); topología 1 proceso 2 caras (loopback embebido como lib; contrato HTTP hacia DayZ intacto, `/set_poll_delay` incluido). **E1 reformulado**: lista canónica = 12 tools (la cuenta "11/6" del architecture §4 era internamente inconsistente) — `vehicle_drive` FUERA (B3 client-auth, diferida a fase client-peer futura), `session_connect/disconnect` FUERA (orquestación host vía `.ps1`/skill), `+bridge_status` (health Python-only). **E2 reformulado**: el "snapshot de ERPCs" se elimina (D-12/T-A dejó el transporte sin RPCs — el riesgo de opcode-drift que lo motivaba ya no existe); en su lugar handshake de versión bridge+juego fail-closed (`version_state` 4-estados); `exec_enforce` entra en F4 OFF-default + allowlist exacta + audit. **E4 precisado**: lock de instancia = bind del puerto + mutex global de tool-calls (verificado que el SDK no serializa). Deuda heredada (scene-freeze, migración `MCPBridge.c`→`MCPJobRunner`) fuera de F4 → backlog. **R22 Codex el mismo día**: approve with minor changes (1 FAIL handshake + 4 WARN + 1 NIT) → R22-F4-001..006 aplicados en plan v2. Plan: `plans/2026-06-10-fase4-mcp.md`. Review: `AI/10_Projects/DayZ_MCP/reviews/2026-06-10-plan-review-fase4-codex.md`.
- **2026-06-09 (Fase 3 — ratificación post-review)**: tras la review adversarial (3 subagentes), el usuario ratificó **SUP-1** (D1 sobre el `Camera` entity, no el indexado — D-11; fila D1 actualizada a la medida vía `GetTransform`), **SUP-2** (transporte T-A poller cliente HTTP, no RPC — D-12) y el diseño (tools `camera_*`/`capture` separadas, `SetOrientation` primario, `MCPJobRunner` base compartida — D-13). Plan v2 + review: `plans/2026-06-09-fase3-visual.md`, `AI/10_Projects/DayZ_MCP/reviews/2026-06-09-adversarial-review-fase3-plan.md`.
- **2026-06-09 (Fase 3 — grill de plan, adjudicado con usuario)**: D1/D2 entran a planificación (research dual fase 0 consolidado, `research/2026-06-09-fase3-visual.md`). **CONFLICT-1 (D2)**: el criterio `<1MB` se reescribe a **presupuesto de tokens ~25k (Claude Code, issue #9152; un screenshot reventó con ~137k)** + downscale agresivo small-by-default; el `<1MB` era de Claude Desktop y no aplica. **CONFLICT-2 (D2)**: `capture_screenshot` es **síncrono con readiness-gate + timeout, sin job-id async** (idiomático en MCP de captura; corrige la anatomía prior-art LL-030 que listaba job-id). Ambos adjudicados vía AskUserQuestion 2026-06-09 (usuario despierto). Derivación host-direct + plan en `plans/2026-06-09-fase3-visual.md`.
- **2026-06-09 (X.5 cierre fase 2)**: R21 del bridge fase-2 procesada → 2 FAIL (P1) corregidos por Codex (X.5 sesión 28), 3 WARN (P2) → backlog (`bug-ledger.md` BUG-010/011/012). **D-10**: el allowlist de `fixture_jsonl.path` es **harden-prefix deny-by-default** (prefijo exacto `$mission:dayz_mcp/` + basename sin `/`, `\` ni `..`), NO igualdad exacta de una ruta — el single-exact rompería los negativos `fixture_not_found`/`parse_error` del harness, que usan rutas distintas bajo el prefijo (ripple R7). Fixture vacío → `parse_error`/`found=false`. Verificado host-direct + re-run in-game `GATE=PASS`/`overall_pass`. C1/C2 siguen ✓.

- **2026-06-08 (fase 2)**: C1/C2 ✓ in-game (run PBO, overall_pass). **Drop de `GetCrosshairObject`** (cliente-only) ratificado — `scene_raycast` usa `from/to` explícitos server-authoritative (cláusula de desafío al architecture §6, confirmada por el spike). Primaria C1 = `RayCastBullet` (normal unitaria; el `dir` de `RaycastRVProxy` no es unitario). Ver decision-log D-09.

- **2026-06-08** (test in-game fase 1, confirmado con usuario) — **B1** y **B2** validados in-game. B2 requirió un fix real: los deadlines del bridge estaban en ticks pero el `OnTick` corre a ~6000-8000 Hz (300 ticks ≈ 50 ms « anim de seat ~2 s) → migrados a **tiempo de pared** (timeslice acumulado). **B3**: probe ejecutado con todos los confounds controlados (fixture_ready=true) → **movimiento client-authoritative confirmado** (motor server-side OK, pero throttle server-side no mueve el coche bajo PHYSICS). Decisión: conducir desde el server **descartado**; la tool de conducción se **difiere a una fase dedicada client-peer**. El Intent "conducir sin input SO" se mantiene como objetivo.
- **2026-06-07** (R22 plan-review fase 1, confirmado con usuario) — **B2** corregido: predicado con `DayZPlayerConstants.VEHICLESEAT_DRIVER` (constante nombrada), NO el literal `==0` (el enum ≠ 0, `dayzplayer.c:674`). **B3** redefinido: fase-1b = PROBE server-side de decisión (coches PHYSICS client-auth, `actionstartengine.c:51-58`); el criterio de producto "mueve el vehículo" queda pendiente de la decisión post-probe (server/client/diferir). El Intent ("conducir sin input del SO") se mantiene.
- **2026-06-06** (R22 plan-review fase 0) — A1 precisado: la "coordenada conocida" la fija el **spawn
  determinista de la mission de test** (no la tool `world_spawn`, que sigue fuera de alcance). Se
  mantiene el umbral <0.5 m. Confirmado con usuario (Cláusula de desafío, R22-004).
- **2026-06-06** — Alcance inicial definido (confirmado con usuario, Grill Modo A). Tool surface
  y fases tomadas de `dayz-mcp-architecture.md` (diseño cerrado, ~2.5M tokens de research +
  spot-check directo de APIs load-bearing). Decisiones de arranque: POC server-side (modded
  MissionServer), transporte HTTP crudo, no-bloqueo probado por tick-counter + RTT.

## Changelog de contrato — 2026-08-18 (`ui_dialog` y `playbook_run`)

Publicación de la fase 4 del plan `plans/2026-08-17-ui-dialog-plan-fusionado.md` §4. No reabre
A–H de arriba: añade superficie y un campo podable en `MCPResult`. Límites y estados copiados
del árbol (no de memoria). Marcado `[EXACT]` = leído en esta copia.

### `ui_dialog` — firma, `kind` y límites `[EXACT]`

Tool MCP (`tools/dayz_mcp/server.py`, `async def ui_dialog`) y comando de puente del mismo nombre (`tools/dayz_mcp/loopback.py`, `CLIENT_COMMANDS`, peer `client`). Exige lease: no está en
`READ_ONLY_COMMANDS` (`tools/dayz_mcp/session_coordination.py:18-38`).

```
ui_dialog(kind, title, message="", fields=None, timeout_s=60.0)
```

| Campo | Regla `[EXACT]` | Sitio |
|---|---|---|
| `kind` | `acknowledge` \| `confirm` \| `form` | `ui_dialog.py`, `KINDS` |
| `title` | str, 1..80 chars **tras strip** | `ui_dialog.py:20-21,97-99` |
| `message` | str, 0..600 chars (sin strip de longitud). **Obligatorio no vacío** (tras strip) en `acknowledge` y `confirm`. Opcional en `form` | `ui_dialog.py:22,101-105` |
| `fields` | solo si `kind="form"` (otro `kind` + `fields` → `bad_args`). Lista de **1..6** objetos | `ui_dialog.py:23-24,118-127` |
| `timeout_s` | número finito **5.0..240.0**; default 60.0 | `ui_dialog.py:28-30,107-116` |

Cada elemento de `fields` acepta en la tool las claves exactas `{id, label}` más opcionales
`{required: bool = true, default: str = ""}` (`ui_dialog.py:34,37,175-198`). Ninguna otra clave
(`ui_dialog.py:156-159`). En el cable Enforce la clave es `default_text` (`default` es reservada):
`bridge_args` traduce (`ui_dialog.py:229-251`; DTO `MCPDialogField` en
`DayZ_MCP\scripts\5_Mission\MCPMessages.c:12-19`).

- `id` casa `^[a-z][a-z0-9_]{0,31}$` y es **único** en la lista (`ui_dialog.py:32,165-167,131-138`).
- `label` 1..60 chars tras strip (`ui_dialog.py:25-26,168-173`).
- `default` / `default_text` ≤ 256 chars (`ui_dialog.py:27,186-196`).
- 7 campos (N+1) → `bad_args` **antes** de encolar (`ui_dialog.py:126-127`).
- Presupuesto Python del puente = `timeout_s + 10.0` (`BRIDGE_SLACK_S`, `ui_dialog.py:31,67-69`)
  → ≤ 250 s, por debajo de `MAX_TIMEOUT_S` 300.0 (`server.py`). El `operation_timeout_s`
  encolado es ese presupuesto, no el timeout del jugador. El sondeo usa
  `WAIT_FOR_MIN_POLL_INTERVAL_S` 0.5 s (`server.py`, constante y bucle de sondeo).

Alcance v1: el cliente local donde corre el puente. Sin `target_player`. Relación con
`notify_players`: no se duplica; el toast vanilla sigue unidireccional. `ui_dialog` existe
cuando hay que **saber** que el jugador respondió.

### Cinco estados terminales `[EXACT]`

`completed` · `cancelled` · `timed_out` · `disconnected` · `rejected`
(`ui_dialog.py:17-19`; contrato `plans/2026-08-18-ui-dialog-contrato-v1.md:157-159`).

`cancelled` y `timed_out` son **respuestas válidas**: `ok` true y `state` lo dice. **Nunca** se
convierten en `choice:"no"` ni en error de tool (`ui_dialog.py:339-343`;
contrato v1 `:157-159` y `:287-288`). Cancelar una confirmación (botón Cancelar) no es «No».
Nada de `values` parciales si el jugador no envía. `timed_out` lo produce el job del cliente
cuando el jugador no contesta; el vencimiento del presupuesto Python **sin** resultado es
transporte (`ToolError("timeout waiting for ui_dialog …")`, `execute_ui_dialog` en `server.py`) y no se falsifica
como `timed_out`.

`rejected` exige `reason:"busy"` (`ui_dialog.py:333-337`): segundo diálogo con uno abierto,
rápido, sin alterar el primero (`MCPClientBridge.c`, `rejected.reason = "busy"`).

### Resultado público (aplanado) `[EXACT]`

El cable anida el desenlace bajo `dialog` porque `MCPResult.state` ya es `ref MCPPlayerState`
(`MCPMessages.c`, `class MCPResult` / `ref MCPDialogResult dialog`). Python (`interpret_result`, `ui_dialog.py:270-365`) valida
el objeto y devuelve al agente:

`{ok, state, dismissed_by?, choice?, values?, values_by_id?, reason?, elapsed_s}`
más passthrough de `id` y `_server` si vienen.

- `values`: array ordenado `{id, value}` en el **orden declarado** de `fields`
  (`ui_dialog.py:368-388`). Python añade `values_by_id` (dict) cuando hay `values`
  (`ui_dialog.py:360-364`).
- `dialog` ausente o no-objeto → `ToolError("bridge_bad_result: dialog missing")`
  (`ui_dialog.py:291-293`).
- `state` fuera del enum, `values` que no casan con los ids declarados (mismo conjunto,
  mismo orden) o `choice` fuera de `yes`/`no` → `ToolError("bridge_bad_result: …")`
  (`ui_dialog.py:295-297,324-328,368-388`).
- Enforce emite strings sin asignar como `""` y arrays como `[]`. Python normaliza **antes**
  de validar: `choice`/`dismissed_by`/`reason` `== ""` → ausente; `values == []` → ausente
  salvo `completed` de `form` (`ui_dialog.py:307-316`). `elapsed_s` sigue obligatorio
  (`ui_dialog.py:299-301`).
- `ok` true en todo estado terminal (contrato v1.1 `:397-398`).

### Dos familias de error `[EXACT]`

1. **`bad_args: <campo> …`** — del llamante, en la tool MCP (`ui_dialog.py:72-73,92-94` y
   el resto de `_bad` / `UiDialogError`; `server.py` lo reexpone como `ToolError` en su manejo de `UiDialogError`).
   En el daemon (`POST /enqueue`) el token es solo `"bad_args"`, sin eco del texto del
   llamante (`ui_dialog.py:203-211,224-225`).
2. **`bridge_bad_result: …`** — del puente / resultado mal formado
   (`ui_dialog.py:254-255,277-280`).

### Cambio observable para un consumidor ya existente

`MCPResult` gana el miembro `ref MCPDialogResult dialog` (`MCPMessages.c`, dentro de `class MCPResult`). Es la
única clave de primer nivel nueva. En Python `dialog` entra en `PRUNABLE_FIELDS`
(`tools/dayz_mcp/result_prune.py:45`): los verbos que no lo rellenan lo omiten (vacío /
`null` = no asignado). **Excepción semántica**: `("ui_dialog", "dialog")` está en
`SEMANTIC_EMPTY_FIELDS` (`result_prune.py:53-55`) — un objeto vacío en `ui_dialog` es un
defecto del puente, no ruido a esconder.

`ui_tree` / `ui_set_text` / `ui_click` / `action_use` / `notify_players` no cambian de
firma ni de semántica por este campo (se poda cuando van vacíos).

### `playbook_run(name, params)` — una línea `[EXACT]`

`playbook_run(name, params)` (`server.py`, `async def playbook_run`) corre un checklist nombrado
(`playbooks/<name>.toml`) contra las tools **ya registradas de la misma sesión/lease**;
no lanza DayZ; tope 32 pasos; `certified` es **siempre false** hoy
(`certified_reason: "no_frozen_registry"`, `playbook_tool.py:25,140-141,244-245`).

### Versión de puente (esta copia) `[EXACT]`

El bump 7→8 que este documento daba por aplazado (D-52) **ya se aplicó**. Verificado
2026-08-20: `MCP_BRIDGE_VERSION = "8"` (`addon/scripts/5_Mission/MCPMessages.c:1`) y
`EXPECTED_BRIDGE_VERSION = "8"` (`tools/dayz_mcp/core.py:17`). Los dos lados coinciden,
que es lo único que este apartado necesita afirmar; el número concreto se lee del
código, no de aquí.

### Qué no entra (plan fusionado §7 + aparcados)

RPC cliente→servidor · jugadores remotos · cola de diálogos · `ui_open` / `ui_close` ·
tres tools públicas · duplicar `notify_players` · un layout por pregunta ·
`CreateWidgets` por apertura · tratar cancelar/timeout como `"no"` · devolver texto
parcial · heredar de Dabs · recarga de layouts en caliente.
