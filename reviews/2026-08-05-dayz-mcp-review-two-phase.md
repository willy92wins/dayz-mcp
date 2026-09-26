# DayZ-MCP — Revisión en dos fases

> **Fecha:** 2026-08-05  
> **Autor:** Grok (sesión de revisión)  
> **Alcance:** cómo funciona el MCP + recomendaciones de valor, y auditoría de bugs / riesgos / optimización.  
> **Fuentes:** `product-spec.md`, `dayz-mcp-architecture.md`, `HANDOFF.md` (snapshot 2026-07-29), bug-ledger (BUG-001…067), skill `dayz-mcp-verify`, código vivo en `DayZ_MCP_dev\tools\dayz_mcp\` + bridges Enforce (`DayZ_MCP\scripts\`).  
> **Qué NO se hizo en esta pasada:** suite pytest, gates in-game, reproducción de `build:true`, verificación de hashes del PBO desplegado.

---

## Índice

1. [Fase 1 — Cómo funciona](#fase-1--cómo-funciona)
2. [Fase 1 — Recomendaciones de funcionalidad](#fase-1--recomendaciones-de-funcionalidad)
3. [Fase 2 — Auditoría](#fase-2--auditoría)
4. [Prioridad sugerida](#prioridad-sugerida)
5. [Verificación de esta auditoría](#verificación-de-esta-auditoría)

---

## Fase 1 — Cómo funciona

### Modelo mental (3 actores)

| Actor | Rol |
|---|---|
| **Daemon MCP** (Python, `:8765`) | Dueño del loopback; cola de comandos; lease FIFO multi-agente; lifecycle de runs |
| **Peer server** (`MCPBridge` en MissionServer) | Spawn, world, raycast, telemetría autoritativa, seat server-side |
| **Peer client** (`MCPClientBridge` + renderer) | Cámara, captura (window-grab), ownership de vehículo, control sostenido |

El bridge **no empuja**: hace poll HTTP async en el tick (`RestContext.GET/POST`, nunca `*_now`) → el sim no se bloquea a 60 Hz.

```
Agente ──stdio──> dayz_mcp --client
                      │ HTTP acreditado (key + provenance de socket)
                      ▼
                 daemon :8765  ←── /poll server + /poll client
                      │
              whitelist + lease gate
                      ▼
           Enforce dispatch en TickScheduler
```

### Capas que importan

1. **Tools tipadas** (`server.py`) — ~30 tools: world / scene / camera / vehicle / session / lifecycle.
2. **Loopback** (`loopback.py`) — colas peer-scoped, TTL de comandos, handshake `ver=`, whitelist.
3. **Coordinación** (`session_coordination.py` ~139 KB) — lease FIFO 120 s, lecturas sin lease, mutaciones con lease.
4. **Lifecycle sellado** — `dayz_test_run` / `dayz_test_stop` por `run_id`; launcher nativo + policy; no acepta paths/PIDs arbitrarios.
5. **Captura visual** — `MakeScreenshot` roto (T165276) → `PrintWindow(PW_RENDERFULLCONTENT)` host-side, JPEG budget-aware.

### Tool surface (estado observado 2026-08-05)

Tools expuestas por el servidor MCP `dayz-mcp` (resumen por dominio):

| Dominio | Tools |
|---|---|
| **Lifecycle** | `dayz_test_run`, `dayz_test_stop` |
| **Session** | `session_acquire`, `session_acquire_wait`, `session_wait`, `session_cancel`, `session_heartbeat`, `session_release`, `session_status` |
| **Bridge** | `bridge_status` |
| **World / scene** | `world_spawn`, `object_delete`, `world_time_set`, `world_weather_set`, `scene_raycast`, `telemetry_read` |
| **Query** | `query_player_state`, `query_all_players`, `query_get_in_condition` |
| **Camera / capture** | `camera_set`, `camera_get`, `capture_screenshot` (en `server.py`; disponibilidad en cliente depende del registro) |
| **Vehicle** | `vehicle_enter`, `vehicle_get_in_client`, `engine_set`, `vehicle_control`, `vehicle_telemetry`, `vehicle_trace`, `vehicle_release` |
| **Misc** | `notify_players` |
| **Breakglass** | `exec_enforce` (OFF por default; flag de arranque) |

Comandos en whitelist del loopback **sin** tool MCP pública (inconsistencia de superficie):

- `vehicle_prepare_fixture` (server; hardcode `MERCEDES_AMGLF`)
- `vehicle_drive` (server; usado vía raw enqueue / conditioning)
- `drive_probe_client` (client; gate interno)

### Lo que ya está fuerte

- Transporte async fail-closed (key, whitelist, version gate).
- Multi-sesión real (broker + lease), no “un Claude = un puerto”.
- Escalera de vehículos owner-side (spawn → render → get-in → drive → trace).
- Doctor, audit JSONL, reclaim de huérfanos, dead-run reaper.
- Suite offline grande (~88 test files; ledger con 60+ bugs, casi todos cerrados).
- Protocolo de sesión documentado (`dayz-mcp-agent-session-protocol.md` + skill `dayz-mcp-verify`).

### Flujos canónicos

**Smoke de objeto (observación + visual):**

1. `dayz_test_run(project, mode=all, …)` → conservar `run_id`
2. `bridge_status` (peers frescos, `version_state=ok`)
3. `world_time_set` + `world_weather_set(overcast=1.0)`
4. `world_spawn` → `camera_set` + `capture_screenshot` (órbita) → `scene_raycast` → `telemetry_read(object_at)`
5. `dayz_test_stop(run_id)` + `session_status` limpio al handoff

**Drivability (peer owner):**

1. Spawn + condicionar (ruedas/fluidos)
2. `vehicle_get_in_client` → `engine_set(start)` → `vehicle_control` sostenido
3. `vehicle_telemetry` / `vehicle_trace`
4. `vehicle_release`

**Coordinación multi-agente:**

- Lecturas puras: sin lease.
- Mutaciones: `session_acquire_wait` → mutar → `session_release`.
- Lifecycle: `dayz_test_run` / `dayz_test_stop` encapsulan lease; no envolver en segundo `session_acquire`.

---

## Fase 1 — Recomendaciones de funcionalidad

Ordenadas por valor / coste. Si solo se hace una: la #1. Si dos: #1 + #3 o #1 + #4 según el dolor del momento.

### 1. `restore_gameplay` como tool pública — la más barata y la que más duele

**Problema:** `camera_set` llama a `SuppressGameplay()` (`PlayerControlDisable` + hide HUD) y **no hay tool MCP** que dispare `RestoreGameplay()`. Solo `drive_probe_client` (interno) lo hace. Hoy, tras un smoke con free-cam, el usuario no puede conducir a mano: hay que relanzar sin `@DayZ_MCP` o reconectar.

**Propuesta:**

- 1 verbo client + 1 tool tipada + test.
- Alinear `vehicle_get_in_client` con el mismo restore (cierra BUG-040).

**Impacto:** cierra un hueco operativo documentado en la skill (2026-07-14) y desbloquea el flujo smoke → test-manual en la misma sesión.

**Anclas:**

- `MCPClientBridge.c` — `SuppressGameplay` / `RestoreGameplay` (~2042–2080+)
- Skill `dayz-mcp-verify` § “No hay tool de restore-gameplay”

### 2. `vehicle_prepare_fixture` genérico + tool MCP

**Problema:** el verbo existe en whitelist y bridge, pero está **hardcodeado a `MERCEDES_AMGLF`** (`loopback.py:173`, `MCPBridge.c:866`). Para condicionar ruedas/fluidos en otros coches se usa el hack `vehicle_enter` + micro-`vehicle_drive` (OnDebugSpawn server-side).

**Propuesta:**

- Generalizar a cualquier classname `CarScript`.
- Exponerlo como tool tipada (hoy no aparece en la surface MCP).

**Impacto:** escalera de aceptación de Forza / SUB_BRZ / LFHeli sin hacks por proyecto.

### 3. Readiness de cliente “Mission ready” (cierra BUG-065)

**Problema:** `dayz_test_run(mode=all)` acredita UDP server + ACK de launch del cliente, **no** que Mission haya compilado/cargado. Resultado: el agente ve `run success` y lanza spawns/capturas contra un cliente aún en carga → timeouts, capturas negras, `version_blocked` intermitente.

**Propuesta:**

- Gate: Mission cargado + `bridge_status.client_peer.last_poll_age_s` fresco + (opcional) player presente.
- Plan externo ya marcado `PASS_READY_FOR_USER_APPROVAL` en el ledger.

**Impacto:** elimina el falso “listo” más caro del lifecycle.

### 4. Telemetría de inventario fiable (BUG-066 a/b/c)

Hoy no se puede auditar “¿tiene slot de capó?” ni inventarios >16 items:

| Defecto | Síntoma |
|---|---|
| API inversa `GetSlotIdCount` / `GetSlotId` | `declared_slots:[""]` en CivilianSedan |
| Mezcla attachments + cargo | consumidor adivina por orden |
| Truncado silencioso a 16 | se pierden items sin flag útil |

**Propuesta:**

- (c) dos líneas: `GetAttachmentSlotsCount` / `GetAttachmentSlotId` (causa raíz ya citada en HANDOFF).
- (a) separar arrays attachments vs cargo.
- (b) total real + flag de truncamiento.

Tocar el bridge exige rebuild de PBO + gate agrupado.

**Ancla:** `MCPBridge.c:1724–1766` (`PopulateTelemetryInventory`).

### 5. Orquestador de smoke de primer nivel

La skill `dayz-mcp-verify` ya define playbooks; falta una tool o subcomando del estilo:

```text
smoke_object(classname, angles=4)
  → { spawn, orbit_pngs[], raycast[], telemetry, verdict }
```

Reduce 8–12 tool-calls + errores de coords (`<x,z,y>` vs `[x,y,z]`) y de settle a un contrato estable. Encaja con R5 (agrupar pruebas) sin ampliar la superficie de riesgo del bridge.

### 6. (Opcional, fase media) input de player acotado

Puertas, inventario, disparo, recarga siguen fuera. Un primer paso seguro sería **solo** `OverrideRaise` / `player_action_start` whitelisted — no raw input SO. Es el siguiente salto de cobertura, pero más caro y más riesgoso que 1–4.

### Resumen de recomendación

| Si el presupuesto es… | Hacer |
|---|---|
| **Una cosa** | `restore_gameplay` + fix BUG-066 (c) en el mismo rebuild/gate |
| **Dos cosas** | Lo anterior + readiness Mission (BUG-065) |
| **Lote de producto** | + `vehicle_prepare_fixture` genérico |

---

## Fase 2 — Auditoría

### P0 / alto impacto operativo (arreglar o no confiar)

| ID | Qué | Evidencia | Riesgo |
|---|---|---|---|
| **`build:true` roto** | `dayz_test_run(build=true)` → `dayz_test_failed` genérico; no escribe PBO | HANDOFF 2026-07-29; reproducido 2× ~16 s; AddonBuilder a mano OK | Todo rebuild vía MCP es mentira; workaround = AddonBuilder staging + hash + run sin build |
| **`except Exception → dayz_test_failed`** | `server.py:1100-1101` y `:1126-1127` tragan la causa real | Código vivo | Diagnóstico ciego (CF junction, build, readiness… todos parecen lo mismo) |
| **Daemon cachea `loopback.py`** | Whitelist en memoria hasta reinicio | LL-223; mtime vs PID medido | Verbo nuevo → `not_whitelisted` aunque el tool esté registrado en el cliente |
| **BUG-066 (c)** | Slots de attachment leídos con API de “slots del item como attachment de otro” | `MCPBridge.c:1739-1742` vs vanilla `GetAttachmentSlotsCount` | Telemetría de slots inútil / engañosa |
| **Sin `restore_gameplay`** | Control del player queda suprimido post-`camera_set` | `MCPClientBridge.c:2042+` + skill | Bloquea handoff a test manual |

### P1 — correctness / falsos verdes

| ID | Qué | Notas |
|---|---|---|
| **BUG-065** | Readiness no espera Mission client | Agente trabaja sobre cliente a medio boot |
| **BUG-061** | `vehicle_trace` cadence ~19.97 Hz < 20; `OnContact` body no observado | Gate G3 incompleto; no es crash, es instrumentación |
| **BUG-040** | `vehicle_get_in_client` no restaura sim tras freecam | Defendible por convención R2.5; frágil |
| **`players:[]` ambiguo** | Null serializado como `[]` en muchos verbos | Consumidor no distingue “cero players” vs “este verbo no rellena” |
| **Coords motor vs log** | Log bridge imprime `<x,z,y>`; tools esperan `[x,y,z]` | Spawn a 6 km de altura → timeout + delete engine (SP-060) |
| **Captura con display dormido** | Frame negro sin ser “render roto” | Skill documenta; la tool no lo detecta |
| **BUG-009** | Autoconexión cliente flaky | Mitigado con `server_wait` alto; sigue open |

### P2 — seguridad / integridad (threat model local, no red)

| ID | Qué |
|---|---|
| **BUG-035** | Version-gate escribible por cualquier key-holder vía `/poll?ver=` |
| **BUG-036** | Peer version-blocked no drena cola → wedge a `MAX_QUEUE=64` |
| **Shared key game+session** | Una key para bridge y agentes; OK single-user, no boundary real |

### P3 — mantenimiento / optimización

| Área | Hallazgo |
|---|---|
| **Módulos monstruo** | `session_coordination.py` ~139 KB, `process_lifecycle.py` ~88 KB, `loopback.py` ~77 KB, `runtime_state.py` ~73 KB, `server.py` ~64 KB — coste de revisión y de bugs de anclas muertas (ledger avisa) |
| **BUG-027** | `timeout_s` sin clamp superior + mutex global → un timeout absurdo con bridge muerto inmoviliza tools |
| **BUG-031** | Validadores duplicados Python↔Enforce; boilerplate lock×N; allowlist no recargable |
| **BUG-006/007** | Deferred desde fase 1: backpressure de cola y `TryInit` RestApi por-tick — **¿siguen vigentes?** (HANDOFF los marca P3 backlog) |
| **runs.json sin poda** | Residual de auditoría Fable; manifests grandes ya rompieron readiness (BUG-055) |
| **`vehicle_prepare_fixture` no expuesto** | En whitelist pero no en `@app.tool` — superficie inconsistente |
| **Product-spec grupo G en ❓** | Drivability real está implementada y parcialmente gateada; el spec está **stale** vs HANDOFF |
| **4 tests flaky** | `test_bug046_startup_deadlock`, `test_task7_review_regressions`, `test_bug046_audit_fault_recovery`, `test_client_mode` (HANDOFF) |
| **Steam DLL churn (clase BUG-054)** | Actualización de Steam invalida clausura del launcher nativo → rebuild CAS obligatorio |

### Lo que NO es bug (limitaciones de engine / diseño)

- `exec_enforce` no ejecuta en server headless (GATE4B-LIM) — el gating + audit sí.
- `MakeScreenshot` roto — window-grab es la vía correcta.
- 1 Steam account = 1 cliente (kick 179) — multiplicidad de `query_all_players` no comprobable en esta caja.
- Spawn pelado de CarScript sin ruedas — esperado, no misalignment.
- Fail-closed al editar código del daemon (BUG-062) — correcto por diseño; el coste multi-agente es el precio.

### Optimizaciones concretas (ROI alto, sin rediseño)

1. **Propagar `error.code` real** en `dayz_test_run` / `dayz_test_stop` en vez de `dayz_test_failed` catch-all — pocas líneas, multiplica la utilidad del diagnóstico.
2. **Clamp `timeout_s`** (p.ej. 1–120 s) en `_timeout` — cierra BUG-027.
3. **Hot-reload o stamp de mtime de `loopback.py`** al servir whitelist, o documentar en `bridge_status` el `daemon_started_at` vs mtime de whitelist (hoy hay que adivinar con `Get-NetTCPConnection`).
4. **Content gate en `capture_screenshot`**: mean luminance / non-black ratio como en fase 3 — falla con `black_frame_suspect` en vez de devolver un PNG negro “OK”.
5. **Poda de runs** en manifest + status por `run_id` (ya parcialmente en readiness) — evita rebrotes estilo BUG-055.
6. **Sincronizar product-spec G\*** y bug-ledger con el estado real del HANDOFF (docs que mienten cuestan sesiones).

### Bugs open relevantes (ledger, extracto)

Estado a 2026-07-29 / lectura 2026-08-05 (no re-verificado in-game en esta sesión):

| ID | Estado resumido |
|---|---|
| BUG-009 | open — autoconexión cliente flaky |
| BUG-027 | open P3 — timeout sin clamp superior |
| BUG-031 | open P3 — maintenance pack |
| BUG-035 | open P2 — version-gate spoofable por key-holder |
| BUG-036 | open P2 — cola no drena con version blocked |
| BUG-038 | open P3 — storm de daemons / Job-Object |
| BUG-040 | open P3 — get-in sin RestoreGameplay tras freecam |
| BUG-061 | open HIGH/GATE — cadence + OnContact |
| BUG-065 | open — readiness no espera Mission client |
| BUG-066 | open — telemetría inventario (a/b/c); (c) con RCA y fix de 2 líneas |
| BUG-067 | mitigado fail-closed; fondo (adoptar run ajeno) abierto / Fase 2 congelada |

Muchos BUG-010…030, 032–034, 037, 039, 042–060, 062–064 están **fixed** o cerrados; ver ledger completo en:

`C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md`

---

## Prioridad sugerida

| Prioridad | Trabajo | Tipo |
|---|---|---|
| **1** | `restore_gameplay` tool + BUG-040 | Feature pequeña + bug |
| **2** | BUG-066 (a/b/c) en un rebuild + gate | Bug de medición |
| **3** | Dejar de tragar excepciones en `dayz_test_*` + diagnosticar `build:true` | Plataforma |
| **4** | Readiness Mission client (BUG-065) | Correctness |
| **5** | `vehicle_prepare_fixture` genérico como tool | Feature |
| **6** | Clamp timeout + content-gate captura | Hardening barato |

**Siguiente paso natural:** plan de implementación acotado del lote 1+2 (un rebuild, un gate agrupado), o investigación de raíz de `build:true` (bloqueante de plataforma más caro; congelado por D-33 si aplica política de no-tocar plataforma).

---

## Verificación de esta auditoría

| Afirmación | Cómo se verificó | Qué NO se verificó |
|---|---|---|
| `dayz_test_failed` catch-all | Lectura `server.py:1100-1101`, `:1126-1127` | Si todos los caminos de worker llegan ahí |
| API slots incorrecta | Lectura `MCPBridge.c:1739-1742` + RCA HANDOFF | Gate in-game post-fix |
| Hardcode MERCEDES | `loopback.py:170-174` + `MCPBridge.c:866` | Otros call-sites de conditioning |
| `RestoreGameplay` no tool | Grep tools + `MCPClientBridge.c:2064` | Si algún raw enqueue lo expone |
| Bugs open citados | Lectura bug-ledger + HANDOFF LIVE-STATE | Reproducción in-game 2026-08-05 |
| Arquitectura 3 actores | Architecture doc + skill + código | E2E live en esta sesión |

---

## Referencias de rutas

| Artefacto | Ruta |
|---|---|
| Este documento | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-08-05-dayz-mcp-review-two-phase.md` |
| Product spec | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md` |
| Arquitectura | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-mcp-architecture.md` |
| HANDOFF | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md` |
| Bug ledger | `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md` |
| Server Python | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\server.py` |
| Loopback | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\loopback.py` |
| Bridge server | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c` |
| Bridge client | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c` |
| Skill verify | skill `dayz-mcp-verify` (skills-plugin / `.grok/skills`) |
| README MCP | `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\README-mcp.md` |
