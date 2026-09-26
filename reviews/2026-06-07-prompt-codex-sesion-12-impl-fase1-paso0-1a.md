# Prompt Codex — sesión 12 · implementación fase 1: Paso 0 (infra) + 1a (world_spawn)

> Patrón: implementation-handoff (post-R22, plan v2 aprobado). Scope-bounded: SOLO Paso 0 + 1a.
> Generado 2026-06-07. 1b (vehicle) = sesión 13.

```
===== PROMPT INICIO =====

Tarea: implementar la fase 1 (Control) de DayZ-MCP. Esta sesión cubre ÚNICAMENTE el Paso 0
(infraestructura) + 1a (world_spawn) del plan v2. NO implementes 1b (vehicle_enter, vehicle_drive
probe) — ni siquiera parcialmente.

## Carga inicial obligatoria (leer antes de tocar nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-07-fase1-control.md
   (PLAN v2 — vinculante, con changelog v2 post-R22 al final; implementa Paso 0 §0.1-0.5 + §1a tal cual).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md
   (criterio B1 = lo que 1a debe cumplir; B2/B3 FUERA de esta sesión).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\dayz-harness-apis.md
   (firmas Enforce verificadas con path:line — NO inventes APIs).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\CLAUDE.md
   (invariantes: async only, Enforce 1.29 SIN ternario ?:, fail-closed, deploy PBO -packonly).
5. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\verified-apis.md
   (APIs durables del proyecto).
6. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\decisions\decision-log.md
   (ADRs vinculantes: P2-4 = NO usar DestroyRestApi; mantener get-or-create).

ARCHIVOS A EDITAR (targets, no carga):
- Bridge Enforce: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\
  (MCPMessages.c, MCPBridge.c, MissionServer.c, MCPCallbacks.c).
- Server Python: C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\
  (mcp_server.py, mcp_client.py).
- Source vanilla (solo para verificar firmas): C:\Users\guill\OneDrive\Documentos\DayZ Projects\scripts\ .

NO releas el research consolidado ni los -claude.md/-codex.md. NO toques el transporte ya probado en
fase 0 (poll/result/callbacks/Shutdown del X.5) salvo lo que el Paso 0 exija explícitamente.

## Alcance acotado — Paso 0 (§0.1-0.5) + 1a (§1a) del plan v2

### Paso 0.1 — DTO args tipado  (MCPMessages.c)
- Añadir `class MCPArgs { string type; ref array<float> pos; int flags; int rotation; int seat; float throttle; float duration; };` y `ref MCPArgs args;` a `MCPCommand`.
- Los campos `seat/throttle/duration` se DECLARAN (para no re-tocar el DTO en sesión 2) pero NO se usan en handlers esta sesión.

### Paso 0.2 — Whitelist  (mcp_server.py:14)
- `WHITELISTED_COMMANDS = {"query_player_state","world_spawn","vehicle_enter","vehicle_drive"}`.

### Paso 0.3 — Backpressure  (MCPBridge.c + mcp_server.py)
- Bridge: en OnPollSuccess (MCPBridge.c:193-199) procesar máx `MAX_DISPATCH_PER_TICK`(=4); el resto a `m_Pending`, drenada en OnTick.
- `MAX_PENDING`(=32); OnTick drena `m_Pending` ANTES de StartPoll; NO StartPoll mientras `m_Pending.Count() > 8`; pausar intake si `m_Pending` llega a MAX_PENDING.
- Server: `_handle_enqueue` (mcp_server.py:109): `len(queue) >= MAX_QUEUE`(=64) → `429 {"error":"queue_full"}`.

### Paso 0.4 — Readiness diferido  (MCPBridge.c)
- `class MCPJob { int id; string kind; ref MCPArgs args; Object subject; int deadline_tick; };`. Mapa `m_Jobs`.
- Dispatch diferido: valida → side-effect → crea MCPJob, NO postea aún. OnTick: por job, IsReady → PostResult(ok) y quitar; tick>deadline → PostResult(timeout) y quitar (liberar `subject`).
- `query_player_state` SIGUE síncrono (no lo metas al mapa). No rompas su camino.

### Paso 0.5 — ValidateSpawnArgs (fail-closed, BLOQUEANTE)  (MCPBridge.c)
- `command.args != null` (null → error "bad_args"); `args.type != ""` y `GetGame().ConfigIsExisting("CfgVehicles " + args.type)` (game.c:611) (no existe → "unknown_type"); `args.pos != null && args.pos.Count()==3` ("bad_pos"), coords finitas y en rango de mundo; conversión guardada `Vector(pos[0],pos[1],pos[2])` (NO ArrayToVec); `flags` 0/ausente → ECE_PLACE_ON_SURFACE, si no-cero validar contra allowlist/mask ("bad_flags"); `rotation` 0 → RF_DEFAULT; `CreateObjectEx` no-null antes de crear job ("spawn_failed").

### Paso 1a — world_spawn handler  (MCPBridge.c, en Dispatch tras :265)
- `else if (command.cmd == "world_spawn")`: ValidateSpawnArgs (si falla → PostResult(error), sin spawn); `Object o = GetGame().CreateObjectEx(args.type, v, flags, rotation)` (game.c:702); MCPJob{kind:"spawn", subject:o}; readiness por `GetObjectsAtPosition3D(pos, 2.0, ...)` (game.c:929) casando `GetType()==args.type`; result ok → `{type, pos_real:o.GetPosition(), found:true}`.

## Tests obligatorios (esta sesión)
- **Python (unittest stdlib)**, en C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\ (o donde ya viva el harness):
  - whitelist: enqueue de los 3 comandos nuevos → aceptado; comando no-whitelisted → 400 "not_whitelisted".
  - queue cap: encolar > MAX_QUEUE → 429 "queue_full".
  - args dict: `args` no objeto → 400 "bad_args".
  - Comando: `python -m unittest discover` desde tools\ → TODOS verdes (pega el output literal).
- **Bridge Enforce**: NO hay test in-game esta sesión (el test agrupado R5 viene tras 1b). Requisito: el
  bridge DEBE compilar sin errores Enforce (sin ternario ?:, async only). Si dudas de una firma, verifícala
  en el source vanilla y cítala; NO la inventes.

## Restricciones críticas (vinculantes toda la sesión)
1. **Async only**: nunca `*_now` en el tick. **Enforce 1.29 NO tiene ternario `?:`** → usa if/else.
2. **Fail-closed (R6)**: ValidateSpawnArgs es obligatorio antes de CreateObjectEx; ningún side-effect sin gate.
3. **NO toques el transporte probado** (poll/result/backoff/Shutdown del X.5) salvo lo que 0.3 exija. **NO uses
   DestroyRestApi** (ADR: rompe el singleton global). Mantén el guard GetRestApi()-first.
4. **Python stdlib only** (sin deps nuevas).
5. **NO implementes 1b** (vehicle_enter, vehicle_drive probe) ni en parcial. TENTACIÓN A RESISTIR: el readiness
   map (0.4) y el DTO args (0.1) ya soportarían seat/throttle; los campos están DECLARADOS pero esta sesión NO
   escribe sus handlers. La instrumentación del probe B3 NO va en esta sesión.
6. **NO improvises fuera del plan**: si algo del plan v2 no encaja (firma que no compila, etc.), NO improvises —
   anótalo en el handoff con la sección del plan, aplica la interpretación conservadora, y márcalo para revisión.
7. **NO self-review / R21 en esta sesión**: termina la implementación y para. La doble revisión es sesión aparte.

## Output esperado al cerrar (bloques A/B/C/D)
### Bloque A — Archivos creados/modificados (paths absolutos + tamaño aprox).
### Bloque B — Output LITERAL de `python -m unittest discover` (no parafraseado) + confirmación de compilación del bridge.
### Bloque C — Hallazgos durante implementación (lo que no encajó del plan; "Sin hallazgos" si ninguno).
### Bloque D — Handoff para sesión 13 (1b): estado, infra reutilizable (DTO/readiness/backpressure listos), deuda.

===== PROMPT FIN =====
```
