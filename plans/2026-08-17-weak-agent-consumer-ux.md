# Handoff — UX de consumidor débil (locales Ollama vs dayz-mcp)

**Fecha**: 2026-08-17
**Estado**: Decidido con el usuario. Pendiente de implementación. No se ha tocado producto.
**Orquestador**: Grok TUI (esta sesión). No es el HANDOFF vivo del repo (`HANDOFF.md`).
**Por qué existe**: se midió que un 27B local ya tropieza con el catálogo y los errores. Si qwen3.5 falla, el consumidor real (cliente MCP que vuelca tools, sin ToolSearch) también.

## Decisión (una frase)

El MCP tiene que hablar el dialecto del junior: **un verbo por trabajo, y el error es el nombre de la siguiente tool**. No se añaden tools de descubrimiento. Se recorta superficie y se reescriben señales.

## Paquete acordado (implementar en este orden)

### P0 — sin tools nuevas

1. **Errores-receta.** `lease_required` debe decir `call session_acquire_wait(purpose=...)`. `version_blocked` / peer stale no puede parecer mismatch de protocolo: `game_not_ready` + motivo (`client_not_polling`, `server_poll_stale`). `wait_for` en timeout: `ok: false` (hoy `ok` es siempre `True`, `server.py` `_wait_for_response` 1104-1111) y el texto nombra `wait_for`, no el comando interno.
2. **Un solo verbo de lease en el catálogo público.** Dejar visibles `session_acquire_wait`, `session_status`, `session_release`. Esconder o marcar *advanced* `session_acquire`, `session_wait`, `session_cancel`, `session_heartbeat`. Hoy `session_acquire` gana porque va primero alfabéticamente y su description (`server.py` 1240) es «Acquire or join the FIFO lease»; `session_acquire_wait` (1258-1262) suena a espera. El runbook dice que acquire/wait son low-level; el catálogo, al oído de un 27B, dice lo contrario.
3. **`bridge_status.ready`.** Un bit + `reason` que un junior no pueda malinterpretar. Confirma el pedido ya archivado `fb-20260817-013638-1b14`. Forma mínima: `{ready: bool, reason: string}`. Reasons cerrados, p.ej. `ready` | `no_run` | `server_poll_stale` | `client_not_polling` | `client_legacy_blocked`. `daemon_modules.stale` debe documentarse en la description: «source newer than daemon, not a crash».

### P1 — extra acordada (sí, después de P0)

**Alias `lease_acquire` → `session_acquire_wait`.** Misma tool, segundo nombre. Description de una línea: «alias of session_acquire_wait». `lease_required` puede apuntar también a ese nombre.

Por qué rinde: en ronda 1 qwen3.5 buscó *exactamente* `lease_acquire`, `claim_lease`, `coordination_claim`. Un alias `lease_acquire` cae entre `inventory_give` y `list_projects`: en el recorte a 6k (21/48 visibles) **sí estaría en la mitad leída**. `session_acquire_wait` no.

No es un wrapper nuevo. No duplicar lógica.

### P2 — una tool nueva, solo si se quiere composición

**`playbook_run(name, params)`** sobre el runner que ya existe (`DayZ_MCP_dev/playbooks/`, hoy `place_safely` en DRAFT). Un 27B no encadena `surface_query` + `scene_raycast` + `query_all_players`; sí puede llamar un checklist con nombre. El nombre `playbook_run` cae junto a `pipeline_*`, también en la mitad visible del catálogo.

No adelantar P2 al P0. No esconder el roce: el playbook cubre *secuencias*, no el lease ni el ready.

### Fuera de alcance (explícito)

- No `search_tools` (otro verbo que hay que descubrir).
- No lease implícito en cada mutación (el lease es el recurso compartido de la caja).
- No cookbook largo en cada description (infla los 10k del list).
- No orquestador delante del MCP que esconda el roce.
- No tocar el formato persistente del inbox (`feedback.jsonl` append-only).
- No relajar fail-closed del lease ni del lifecycle (`dayz_test_run` / `dayz_test_stop`).
- No editar `HANDOFF.md` vivo como si este documento lo sustituyera.

---

## Qué se midió

Dos rondas el 2026-08-17. Mismos tres modelos, mismo harness en `%TEMP%`, mismo brief. Ronda 2 = idéntica salvo que `mcp_list_tools` / `mcp_describe_tool` **no se recortan**.

| Modelo | Rol | R1 (lista 6k → 21/48) | R2 (48 tools, 10318 chars) |
|---|---|---|---|
| `gemma4:26b` | consumidor frío | No ve `wait_for`. Usa `stale[]` como causa. Archiva 2. | Ve y llama `wait_for`. No archiva. Informe vacío. |
| `qwen3.5:27b` | mutación | Inventa `lease_acquire` / `claim_lease` / `coordination_claim`. Concluye que no hay tool de lease. | Describe y llama `session_acquire` (low-level, `status=active`). **Nunca** describe `session_acquire_wait`. Libera el token a la segunda (`session_release` sin token → pydantic nombra el campo). |
| `qwen3.8:27b` | smoke + inbox | Diagnostica «no hay is_game_alive» en thinking y no archiva. `content=""`. | Archiva 1 hallazgo nuevo y entrega informe. |

Catálogo stdio del 17/08: **48 tools**. JSON de solo `name`+`description` = **10318** caracteres. Corte a 6000 deja visibles, en orden alfabético, de `action_use` a `query_all_players` (21). Fuera: todo `session_*`, `wait_for`, `world_spawn`, `telemetry_read`, `scene_raycast`, `surface_query`, `ui_*`, `vehicle_*`.

Ningún tool name contiene `lease` salvo `session_release` y `vehicle_release`.

`list_projects` ya existe en el catálogo (ronda 2). Ninguno de los tres lo usó.

Durante las sondas otra sesión tenía el run `30bb95a2` (`@DayZ_MCP`, solo server PID 19956). No se adoptó ni se paró. En ronda 2 el server dejó de pollear (`last_poll` 52 s → 745 s) con `version_state=ok`. El lease de qwen3.5 se liberó: `session_status.owner` volvió a `null`.

### Anclas de código (leídas, no de memoria)

| Hecho | Sitio |
|---|---|
| `wait_for` timeout → `ok: True`, `timed_out: not satisfied` | `tools/dayz_mcp/server.py` `_wait_for_response` 1104-1111 |
| `session_acquire` description pública | `server.py` 1240 |
| `session_acquire_wait` description pública | `server.py` 1258-1262 |
| `wait_for` description ya nombra el enum y default 180 | `server.py` 2151-2168 (parcialmente parcheado respecto a sonda B de madrugada) |
| Inbox: title ≤120, body ≤8000, `bad_args` sin nombrar campo | `tools/dayz_mcp/inbox.py` 55-59 |
| `--client-platform` ∈ {claude, codex, unknown} — no hay `grok` | `tools/dayz_mcp/server_cli.py` 57 |
| Playbooks (no son tool MCP) | `DayZ_MCP_dev/playbooks/README.md`, runner en `playbooks/runner.py` |

### Buzón (ids de esta tanda)

**Ellos (ronda 1)**

- `fb-20260817-100822-0c07` gemma — `stale` vs `version_state ok` (causa mal atribuida)
- `fb-20260817-100956-dd93` gemma — `query_all_players` → `daemon_unavailable`
- `fb-20260817-101741-f909` / `fb-20260817-102125-8295` qwen3.5 — teleport/spawn piden lease y el schema no lo dice

**Revisión ronda 1 (Grok)**

- `fb-20260817-104756-4cc4` — 48 tools / 10k / un junior solo ve la primera mitad
- `fb-20260817-104756-09aa` — `lease_required` no nombra `session_acquire_wait`
- `fb-20260817-104756-5640` — `wait_for` timeout devuelve `ok:true`
- `fb-20260817-104756-aa92` — no hay `ready`; confirma `fb-20260817-013638-1b14`

**Ellos (ronda 2)**

- `fb-20260817-105417-29a0` — teleport pide lease (ahora nombra `session_acquire`)
- `fb-20260817-105446-6dfb` / `fb-20260817-105528-d6e4` — timeout teleport/spawn, peer muerto, `version_state=ok`
- `fb-20260817-111108-0c77` qwen3.8 — `wait_for` en `isError` nombra `query_all_players` (segundo modo de fallo, distinto de `-5640`)

**Revisión ronda 2 (Grok)**

- `fb-20260817-111354-2a2e` — con catálogo entero el junior elige `session_acquire`, no `acquire_wait`

Pedido previo que este paquete cierra en parte: `fb-20260817-013638-1b14` (ready + list_projects; list_projects ya aterrizó).

---

## Contrato hacia adelante (lo que consume la implementación)

Nombres **exactos** a introducir o cambiar. No inventar otros.

| Símbolo | Tipo | Contrato |
|---|---|---|
| `session_acquire_wait` | tool pública | Sigue siendo el acquire canónico. Description: añadir «preferred; use this, not session_acquire». |
| `lease_acquire` | alias P1 | Mismo handler que `session_acquire_wait`. Description: `alias of session_acquire_wait`. |
| `session_acquire` / `session_wait` / `session_cancel` / `session_heartbeat` | low-level | Fuera del `tools/list` por defecto, **o** description que empiece por `LOW-LEVEL; prefer session_acquire_wait`. Preferible esconderlos: si se ven, qwen elige `session_acquire`. Los clientes que ya los llaman por nombre deben seguir pudiendo (no borrar el handler). |
| `session_status` / `session_release` | públicas | Se quedan. `session_release` ya nombra `lease_token` vía pydantic si falta — no empeorar eso. |
| `lease_required` | ToolError | Texto: `lease_required: call session_acquire_wait(purpose=...)` (o `lease_acquire`). |
| `game_not_ready` | ToolError o campo | Sustituye o envuelve `version_blocked` cuando no hay peers vivos. No mentir «mismatch de versión». |
| `bridge_status.ready` | campo nuevo | `{ready: bool, reason: <enum cerrado>}`. Additive. No romper el resto del JSON. |
| `wait_for` resultado | JSON | `ok` es `true` solo si `satisfied`. Timeout → `ok: false`, `timed_out: true`. Si el probe interno muere, el error nombra `wait_for`, no `query_all_players`. |
| `playbook_run` | tool P2 | `name: str` (id del diccionario) + params. No lanza DayZ. Reusa `playbooks/runner.py`. |
| Descriptions de mutación | texto | `player_teleport`, `world_spawn` y cualquier otra mutación: una línea «requires an active lease (session_acquire_wait)». |

`instructions` del FastMCP (`server.py` 1227-1230) hoy solo mencionan `dayz_test_run` / `dayz_test_stop` / `bridge_status`. Añadir una frase: «Mutations need session_acquire_wait; check bridge_status.ready first.»

### Persistencia / rollback

- P0 y P1 no tocan formato persistente del inbox ni del lease.
- `bridge_status.ready` es additive: un cliente viejo ignora el campo.
- Alias: additive. Quitar el alias es rollback.
- Esconder tools del list: rollback = volver a registrarlas. No borrar código.
- Si P2 escribe algo, que sea solo invocación; el TOML de playbooks no cambia de schema en este handoff.

### Tests mínimos (antes de gate in-game)

- Unit: `lease_required` contiene `session_acquire_wait` o `lease_acquire`.
- Unit: `_wait_for_response(..., satisfied=False)` → `ok is False`.
- Unit: `tools/list` **no** incluye `session_acquire` (o su description empieza por `LOW-LEVEL`) y **sí** incluye `session_acquire_wait` y, en P1, `lease_acquire`.
- Unit: `bridge_status` con peers stale → `ready is False` y `reason` ∈ enum.
- No tautología: una fixture con server fresco + client fresco debe dar `ready is True`.
- P2: fixture `place_safely` `--fixtures` sigue en verde; `playbook_run` con name desconocido → `bad_args` que nombra el campo.

### Gate de producto (sonda débil, no frontier)

Repetir **solo qwen3.5:27b** con el harness de `%TEMP%` **sin recorte**, mismo prompt de mutación (`prompts/qwen35.md`):

- PASS si llama `session_acquire_wait` o `lease_acquire` (no `session_acquire`, no inventa `claim_lease`).
- PASS si ante `lease_required` el error le basta (no busca un tercer nombre).
- PASS si con client `legacy_blocked` / peer stale **no** declara el juego listo.
- El harness sigue bloqueando `dayz_test_run` / `dayz_test_stop`.

No hace falta la terna completa para el gate. gemma y qwen3.8 son evidencia, no el criterio de cierre.

---

## Cómo se verificó esta tanda (y qué no)

**Verificado**

- Catálogo 48 / 10318 chars / 21 visibles tras corte 6k: script `measure_list.py` + `measure_clip.py` sobre `out/qwen35/catalog.json`.
- `ok: true` en timeout de `wait_for`: lectura de `server.py` 1104-1111.
- qwen3.5 r2 llamó `session_acquire` y obtuvo `status=active`: `out2/qwen35/mcp_calls.json`.
- `session_acquire_wait` no aparece en ninguna llamada de r2 (grep de `out2/`).
- Lease liberado al final: `session_status` con `owner: null` tras r2.
- Inbox: altas anteriores, ids citados arriba.

**No verificado**

- Qué pasa si se esconde `session_acquire` y qwen3.5 *aún así* no encuentra `session_acquire_wait` (el gate de arriba lo cierra).
- Cliente MCP que vuelca **schemas completos** de 48 tools (peor que 10k de una línea). El harness usó list+describe+call, no dump nativo.
- Mutación in-game feliz: el server del run ajeno se quedó sin poll en r2.
- Que FastMCP soporte alias nativo vs un segundo `@app.tool` que delega. Eso lo decide el implementador con la firma real de `mcp==1.27.2` ya en `.venv-mcp`.

---

## Artefactos de la sonda (no son producto)

Harness y transcripts (no promocionar al repo):

```
C:\Users\guill\AppData\Local\Temp\weak-mcp-probe-20260817\
  weak_mcp_probe.py
  run_r2.py
  prompts\gemma4.md
  prompts\qwen35.md
  prompts\qwen38.md
  out\          ← ronda 1 (lista recortada)
  out2\         ← ronda 2 (lista completa)
```

Ollama usados: `gemma4:26b`, `qwen3.5:27b`, `qwen3.8:27b` (el más capaz local). Un modelo a la vez (`num_ctx=49152`). Lifecycle (`dayz_test_*`) bloqueado en el harness.

Protocolo de caja: `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`.

---

## Reparto sugerido

P0+P1 son Python de `tools/dayz_mcp/server.py` (+ tests en `tools/tests/`). Encajan en G7 → Codex por defecto, receptor que re-lea este archivo y los ids del buzón.

P2 es feature distinta (nueva tool + wiring al runner). Spec corta propia si se llega; no mezclar en el mismo PR que esconder `session_acquire`.

No reiniciar el daemon a ciegas mientras otras sesiones usen el puerto 8765 (`daemon_module_stale` / LL-223). Anunciar el edit de `server.py`.

## Criterio de «hecho»

El usuario da el visto bueno a este documento. La implementación no empieza en esta sesión. Cierre de P0+P1 = tests de arriba verdes **y** sonda qwen3.5 sin recorte en PASS. P2 es opt-in posterior.
