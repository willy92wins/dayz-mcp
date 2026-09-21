# Plan — Refactor broker/daemon (DayZ-MCP multi-sesión)

> **Estado: IMPLEMENTADO + verificado offline (2026-06-23).** Suite 113/113 + E2E binario real 5/5
> (`tools/_broker/e2e_daemon.py` → `e2e_result.json`). Pendiente: gate in-vivo (2 sesiones Cowork
> reales) — usuario · review R21 del código. Plan aprobado por el usuario sin ediciones.

## Context

Hoy cada sesión Cowork arranca su propio `dayz_mcp` que **bindea exclusivamente
`127.0.0.1:8765`** (E4 lock, `ExclusiveThreadingHTTPServer.allow_reuse_address=False`,
[loopback.py:405-410]). Solo una sesión gana el puerto; las demás fallan el bind →
`SystemExit(2)` ([server.py:322-324]) → se quedan **sin tools**. El juego DayZ sondea
UNA URL fija (`http://127.0.0.1:8765/`, `pollHz 5`), así que "más puertos" no sirve (una 2ª
instancia en otro puerto es un bridge muerto: el juego nunca la sondea).

**Objetivo**: que VARIAS sesiones tengan las tools cargadas a la vez sobre un único juego.
**Enfoque (decidido con el usuario)**: un **daemon** standalone dueño de `:8765` y único
que habla con el juego; cada `dayz_mcp` de sesión corre en **modo cliente** y proxya por
HTTP (`POST /enqueue` + `GET /await`) contra el daemon. El daemon serializa los comandos.
**Python-only**: el juego sigue sondeando un único `:8765` (= el daemon), Enforce/PBO intactos.

El loopback **ya** exponía el contrato HTTP que el cliente reusa (`/poll` juego, `/enqueue`,
`/await`, `/result`); el modo cliente solo cambió el transporte de las tools de **in-proceso**
([server.py:168,188]) a **HTTP** contra el daemon, + un nuevo `/status`.

## Decisiones resueltas (AskUserQuestion 2026-06-23)

- **(a) Lifecycle** → **Auto-spawn lazy** por la 1ª sesión: lanza el daemon DETACHED (sobrevive a
  su sesión); las demás lo descubren con `GET /status`. Self-healing: si el daemon murió por idle,
  la siguiente llamada lo re-spawnea. Apagado = **idle self-shutdown reusado**.
- **(b) Fallback** → **Cliente+daemon por defecto, `--embedded` conservado** (bare = embedded).
- **(c) Concurrencia** → **First-come serializado** (lock por comando del daemon) + limitación
  documentada "conduce una a la vez". Sin lease en v1.
- **(d)** Version-gate, exec chokepoint, lock E4 y orphan-guard → en el **daemon**; el cliente proxya.
- **(e)** El comando registrado gana `--client`; install-mcp.ps1 lo registra.

## No-regresión (verificado)

- Gates in-game **0-3** lanzan `mcp_server.py` (loopback bare, `main()` sin validador) →
  cambios a `loopback.py` aditivos y guardados por "validador presente" → comportamiento estable.
- Bare `-m dayz_mcp` (sin flag) **sigue EMBEBIDO** → registración/gates 4A/4B se re-corren idénticos.
  El broker es opt-in vía `--client`. Suite base 91/91 verde tras los cambios.

## Arquitectura (3 procesos)

```
Sesión A ─ dayz_mcp --client ─┐  (FastMCP stdio; NO bindea; HTTP a :8765)
Sesión B ─ dayz_mcp --client ─┼─ POST /enqueue + GET /await + GET /status ─► DAEMON :8765
Sesión C ─ dayz_mcp --client ─┘                                               │ /poll /result
                                       DayZ (server peer + client peer) ◄──────┘ (sin cambios)
```

- **DAEMON** (`-m dayz_mcp --daemon`): dueño de `:8765`. ServerState + version-gate + exec
  chokepoint + lock E4 + idle watchdog. **NO** parent-death watchdog (debe sobrevivir al spawner).
- **CLIENTE** (`-m dayz_mcp --client`): FastMCP stdio. No bindea → sin orphan-guard.
  `capture_screenshot` sigue **local** (window-grab de la sesión, no pasa por el daemon).
- **EMBEDDED** (`-m dayz_mcp --embedded`, = bare default): camino de hoy intacto.

## Implementado

- `dayz_mcp/core.py` **(nuevo)** — `version_state_for`, `build_status`, `load_exec_allowlist`,
  `make_exec_auditor`, `EXPECTED_BRIDGE_VERSION` (fuente única; server.py los reexporta/delegа).
- `dayz_mcp/loopback.py` — `ServerState._last_client_request_at`+`touch_client()`+en snapshot;
  check de versión en `enqueue_command` → `409 version_blocked` (guard validador presente);
  `GET /status` (`_handle_status`) + `touch_client` en `/enqueue`/`/await`/`/status`;
  `/await?remove=1` (evita fuga de results en el daemon); `status_provider` inyectable.
- `dayz_mcp/daemon.py` **(nuevo)** — `run_daemon` (discovery `/status` → exit0 si hay sano; bind con
  reclaim health-gated; idle watchdog con métrica que incluye clientes; sin parent-death watchdog;
  block en `stop.wait()`); `build_server_state`, `make_status_provider`, `build_daemon_argv`,
  `spawn_detached` (Windows DETACHED + BREAKAWAY_FROM_JOB con fallback).
- `dayz_mcp/orphan_guard.py` — `probe_status_healthy`; `try_reclaim_unresponsive_listener`
  (reclaim del daemon por SALUD, no por parentesco). `try_reclaim_port`/`should_reclaim_listener`
  (embedded/C1) sin tocar.
- `dayz_mcp/server.py` — `ServerConfig.mode`; `parse_args` `--client`/`--daemon`/`--embedded`
  (bare=embedded); `run()` despacha por modo; `ClientRuntime` (HTTP + discovery/spawn lazy +
  refused→spawn+retry); `bridge_status_payload` async en ambos runtimes; `Runtime` delega en core.
- `install-mcp.ps1` — registra `--client`; flags de policy del daemon viajan en el comando cliente.

## AC + docs

- **product-spec.md**: grupo **F (F1-F5)** + Changelog 2026-06-23; E4 reencuadrado; exclusión
  "multi-instancia 0-3" retirada.
- **decision-log.md**: D-14 (broker). **HANDOFF.md**: LIVE-STATE sobrescrito.

## Verificación

1. **Suite**: `python -m unittest discover -s tests -t .` desde `tools` con `.venv-mcp` → **113/113**.
   Nuevos: `test_daemon.py` (/status, 409 version_blocked, idle incluye clientes, probe, reclaim
   health-gated, build_daemon_argv) + `test_client_mode.py` (round-trip, 2 clientes/1 daemon,
   version_blocked, bridge_status, business-error, timeout, build_app wiring, refused→spawn+retry).
2. **E2E binario real**: `_broker/e2e_daemon.py` → **5/5** (P1 bind+/status, P2 round-trip,
   P3 2 clientes concurrentes, P4 idle libera puerto exit0, P5 sobrevive a la salida del spawner).
3. **Gate in-vivo (usuario)**: 2 sesiones Cowork reales con tools `dayz-mcp` cargadas a la vez
   (ToolSearch, NO `claude mcp list`) sobre un juego; matar la sesión spawner y ver que las otras
   siguen operativas (o re-spawnean). Verifica supervivencia bajo el Job-Object de Cowork.
4. **R21** del código (infra/tooling, no R9): el usuario decide R22-del-plan o R21-del-código.

## Fuera de alcance / riesgos

- Sin lease de "conductor" en v1: cruce cámara/captura entre sesiones es limitación documentada.
- No se tocó Enforce/PBO, el shim bare, `record_poll`, el exec chokepoint, ni los gates 0-4/R21-X.5.
- Supervivencia del daemon al Job-Object de Cowork = medido best-effort offline (P5 detached OK);
  el caso real es el gate in-vivo. Degradación elegante: aun si muere con el spawner, self-healing.
