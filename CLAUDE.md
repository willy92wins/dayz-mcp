# CLAUDE.md — DayZ-MCP

> Contexto específico del proyecto. Claude (Cowork) y Codex lo leen al entrar.
> Reglas globales del pipeline (roles, doble revisión, grill, R1-R26) NO van aquí:
> están en `C:\Users\guill\ObsidianVault\AI\00_System\workflow.md` (autocargado).

## Resumen
Servidor MCP que expone **DayZ (DayZDiag)** como **tools tipadas** para que un agente
conduzca el juego y extraiga datos estructurados — **server-authoritative, sin teclas SO
ni OCR**. Control + datos son **engine-native** (CreateObjectEx, StartCommand_Vehicle,
Car setters, RestApi async, RaycastRVProxy, SetTimeMultiplier). La captura visual es la
única pieza no-native: `MakeScreenshot` está roto (T165276) → se hace por **window-grab**
externo del cliente renderizado (pasivo, solo lee píxeles). Arquitectura cerrada y **en
producción**: 71 tools registradas (corregido 2026-09-29) y puente en `MCP_BRIDGE_VERSION = "10"`
(`addon/scripts/5_Mission/MCPMessages.c:1`). Las 5 fases del plan original están cerradas.

## Definición de Producto Final
**Qué es "terminado" → [`product-spec.md`](product-spec.md)** — contrato de aceptación
(11 tools en 6 dominios en el alcance original —hoy 71 registradas—, seguridad fail-closed, captura visual, criterios verificables por
fase, fuera de alcance, paridad). Leerlo antes de planificar cualquier fase. El Grill de
plan (Modo B) exige que cada fase trace a un criterio de ahí.

## Diseño y APIs (fuente)
- [`dayz-mcp-architecture.md`](dayz-mcp-architecture.md) — arquitectura (3 actores), tool
  surface, plan por fases, transporte, seguridad, riesgos. **EMPIEZA AQUÍ.**
- [`dayz-harness-apis.md`](dayz-harness-apis.md) — 128 símbolos Enforce verificados con
  `path:line` contra el source vanilla bajo `..\scripts\`.

## Stack técnico
- **Bridge in-game**: Enforce Script, `modded class MissionServer` (server-side). Despacha
  comandos en `OnUpdate`→`TickScheduler`. Transporte vía `RestApi` async (HTTP cliente).
- **Servidor**: Python. Wrapper MCP stdio sobre **FastMCP** (`tools/dayz_mcp/server.py:20`);
  el `http.server` crudo del POC sigue dentro como loopback (`tools/dayz_mcp/loopback.py:13`,
  endpoints `/poll` `/result` `/enqueue` `/await` `/status` `/set_poll_delay`).
- **Transporte**: HTTP sobre `127.0.0.1`. El mod hace `GET`/`POST` async; el servidor Python
  es el endpoint pasivo. El mod **pull**ea comandos y **push**ea resultados.

## Convenciones del proyecto (verificadas, NO asumir)
- **Async only**: nunca `RestContext.*_now` dentro del tick (bloquean el sim 60Hz por el RTT).
  Solo `GET(cb,req)` / `POST(cb,req,data)` con callback (restapi.c:103/123).
- **Fail-closed desde el día 1 (R6)**: bind `127.0.0.1`, API-key por request, whitelist de
  comandos. Sin key o key incorrecta → 401. Comando fuera de whitelist → rechazado.
- **API-key en query string, NO en header**: el mod no puede setear `Authorization` —
  `RestContext.SetHeader()` es **solo Content-Type** (restapi.c:135-141). La key viaja en
  `?key=…`. (Caveat fase 4: puede filtrarse a logs; con bind 127.0.0.1 la exposición es local.)
- **Server-authoritative**: la posición/estado se leen en `MissionServer` (autoritativo), no
  en el cliente. `g_Game.GetPlayers(out array<Man>)` (game.c:947) + `Object.GetPosition()`
  (object.c:293).

## Estructura del repo
```
DayZ_MCP/            (compilable, → @DayZ_MCP)  → $PBOPREFIX$, config.cpp, scripts/5_Mission/
                                                  (el bridge; lo crea Codex desde el plan)
DayZ_MCP_dev/        (este, NO va al PBO)        → CLAUDE.md, product-spec.md, HANDOFF.md,
                                                   dayz-mcp-architecture.md, dayz-harness-apis.md,
                                                   plans/, reviews/, decisions/, tools/ (server Python + ps1)
```

## Estado actual
> El estado vivo (qué funciona, blocker, próxima acción, invariantes cerradas) vive en el
> **header vivo** de [`HANDOFF.md`](HANDOFF.md) (bloque `<!-- LIVE-STATE -->`), que el hook
> `SessionStart` inyecta al arrancar y se sobrescribe en cada cierre. El detalle por sesión
> está en `AI/30_Sessions/`. Ver `workflow.md` §"Arranque de sesión (bootstrap)".

## Gotchas conocidos (verificados in-game / en source)
- **`MakeScreenshot` roto en diag** (T165276): no escribe `.dds` ni en el exe diag. La vía
  visual es window-grab externo (`Graphics.CopyFromScreen`, validado: meanB 65, nbRatio 0.999).
  RenderTargetWidget es display-only (sin readback); RestApi no materializa el framebuffer.
- **`SetHeader` ≠ headers arbitrarios**: solo Content-Type → key en query string (ver arriba).
- **`*_now` bloquea el tick**: usar siempre callbacks async.
- **Filepatching server-side aún sin validar**: la corrida @MCPTest (en `..\MCPTest\`) fue
  client-side; el POC server-side es la 1ª validación del filepatching en el server diag.
- **`SetTimeMultiplier(0)` congela TODA la sim** (animaciones incluidas): condicionar la escena
  DESPUÉS de sentar/animar, nunca antes de una animación pendiente.

## Modos de ejecución (broker — 2026-06-23, D-14)

`-m dayz_mcp` tiene **tres modos** (`server.py` parse_args; bare = embedded):
- **`--client`** (lo que registra `install-mcp.ps1`): NO bindea; proxya por HTTP (`/enqueue`+`/await`+
  `/status`) al daemon, lo **spawnea lazy detached** si no responde, re-spawn on connection-refused.
  Permite N sesiones Cowork con tools a la vez sobre un juego. `capture_screenshot` sigue local.
- **`--daemon`**: el dueño standalone de `:8765`; único que habla con el juego. Posee version-gate,
  exec-chokepoint, lock E4 y el idle watchdog.
- **bare / `--embedded`**: camino single-sesión de hoy (bindea + ambos watchdogs). **Se queda como
  default a propósito** → los gates in-game 0-4 que lanzan el binario sin flag corren idénticos.

**Invariante dura (LL-156)**: el daemon **DEBE sobrevivir a la sesión que lo lanzó** → NO arma
parent-death watchdog y **NO** es reclamable por liveness de ancestro (eso mataría un daemon sano).
El discriminador del reclaim del daemon es **responder `/status` sano** (`orphan_guard.probe_status_healthy`
/ `try_reclaim_unresponsive_listener`), NO el parentesco. El reclaim por-ancestro (`try_reclaim_port`,
C1 del embedded) NO se toca — son discriminadores SEPARADOS por modo. Cambios broker = Python-only
(Enforce/PBO intactos: el juego sigue sondeando un único `:8765` = el daemon).

## Enlaces
- Estado vivo / handoff: [`HANDOFF.md`](HANDOFF.md)
- Plans: `plans/` · Reviews (inbox Codex): `reviews/` · Decisiones: `decisions/`
- Infra de test reutilizable: `..\MCPTest\` (@MCPTest client-side + mcp-shot-test.ps1 +
  mcp-windowgrab-test.ps1)
- Memoria vault: `C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\` (a crear en el cierre)

## DayZ MCP — sesión compartida
1. Adquirir lease antes de mutar o gestionar procesos.
2. Liberarlo en cuanto termine la secuencia exclusiva.
3. No matar procesos DayZ directamente; usar el lifecycle guard.
4. Ejecutar `session_status` antes del handoff y documentar cierres degradados.
Runbook: `C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`.
