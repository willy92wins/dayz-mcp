# Feature Spec: Daemon dead-run reaper + safe non-TTY reconcile

**Mod / PBO**: N/A — Python daemon (`DayZ_MCP_dev/tools/dayz_mcp/`), no Enforce/PBO change.
**Date**: 2026-07-16
**Status**: Draft
**Plan**: incidente ghost-run `ae40…` (crash CE `storage_1`) + auditoría `reviews/2026-07-16-fable-full-audit.md` F-08/F-07/F-05.

## Context / Why

Un crash de DayZ (aquí: `storage_1` corrupto de CF/CE, ajeno al MCP) deja un run en el manifiesto apuntando a PIDs muertos. Verificado en `%LOCALAPPDATA%\DayZ_MCP\runs.json`: run `ae40169081d84de9b7ca7ad56b8fc1ec` = `RUNNING_IDLE`, owner null, processes `[48976 server, 91888 client]` (ambos muertos). Ese run **bloquea permanentemente** la caja: `start`→`active_run_exists`, `adopt`→`process_identity_mismatch`, `stop`→`run_not_adopted`, y **un restart del daemon NO lo limpia** (`recover_after_restart` deja `RUNNING_IDLE` intacto, `process_lifecycle.py:257-270`). Hoy solo lo desbloquea `admin_reconcile`, que es TTY-gated (`admin_cli.py:61`) → exige un humano. El objetivo es que se auto-repe por timeout y que un agente pueda repararlo sin TTY, **sin** debilitar el guard de kill (nunca reapear un proceso vivo ni un PID reusado).

## Acceptance Scenarios *(mandatory)*

1. **Given** un run `RUNNING_IDLE` cuyos ProcessRecords tienen PIDs que ya no existen en el SO,
   **When** transcurre ≤ `REAP_INTERVAL_S` (un tick del reaper) sin intervención humana,
   **Then** el run pasa a `EXITED` (owner/processes vacíos), se emite un evento de audit `run_reaped`, y un nuevo `start` del mismo mod deja de dar `active_run_exists`.
   - **Repro in-game**: lanzar un run gestionado (DayZDiag) vía `lifecycle_cli start`; liberar el lease (queda `RUNNING_IDLE`); matar los DayZDiag por fuera (simula el crash); esperar un tick; `session_status`/`doctor` muestran el run `EXITED` y un `start` nuevo procede.

2. **Given** un run cuyo PID fue **reusado** por un proceso distinto (mismo PID, distinto creation-time/fingerprint),
   **When** el reaper evalúa ese run,
   **Then** **NO** lo repea (queda para revisión manual), porque `_reconcile_survivors` devuelve `process_identity_mismatch`, no "muerto".
   - **Repro in-game**: forjar un ProcessRecord con un PID que ahora pertenece a otro proceso vivo; correr el reaper; el run permanece sin cambios y no se mata nada.

3. **Given** un run con al menos un proceso **vivo** que aún coincide en identidad,
   **When** el reaper evalúa ese run,
   **Then** **NO** lo repea (hay supervivientes).
   - **Repro in-game**: run gestionado con DayZDiag vivo; correr el reaper; el run sigue `RUNNING`/`RUNNING_IDLE`, procesos intactos.

4. **Given** un run all-dead y un agente (sin TTY) con lease activo,
   **When** el agente llama la vía no-TTY `lifecycle_reap`/`reap --run-id`,
   **Then** el run pasa a `EXITED` inmediatamente (sin esperar el tick), y la **misma** vía sobre un run con procesos vivos o diag ambiguo **rechaza** (`run_not_reapable`) sin tocar nada.
   - **Repro in-game**: tras el crash, un agente con lease invoca la tool; el run se repa; repetir con un run de procesos vivos → rechazo.

5. **Given** un run all-dead cuando existe presencia retail (cuarentena),
   **When** el reaper (auto o agent-callable) evalúa,
   **Then** **NO** repea y audita `cleanup_degraded`/skip por `retail_quarantine` (consistente con el resto del lifecycle).
   - **Repro in-game**: offline (inyectar `retail_probe` con presencia) — no requiere ciclo in-game.

## Success Criteria *(mandatory)*

- **SC-001**: un run activo cuyos **todos** los PIDs devuelven `process_not_found` (guard exit_code 4) y con diag snapshot conocido-limpio pasa a `EXITED` en ≤ `REAP_INTERVAL_S` s (default `ASSUMED 30.0`). Verificable: unittest con fake guard/clock.
- **SC-002**: un run con `_reconcile_survivors` → `process_identity_mismatch` **nunca** se repea (0 transiciones a EXITED). Verificable: unittest.
- **SC-003**: un run con ≥1 superviviente vivo **nunca** se repea. Verificable: unittest.
- **SC-004**: el reaper **nunca** invoca `guard.terminate`/`kill` (0 llamadas de terminación en cualquier ruta del reaper). Verificable: grep + fake guard con contador `terminated`.
- **SC-005**: la vía `lifecycle_reap` no-TTY solo triunfa sobre runs all-dead; sobre un run con vivos/diag-ambiguo devuelve `run_not_reapable` (409) sin mutar el manifiesto. Verificable: unittest.
- **SC-006**: `admin_cli`/`/admin/reconcile` (el force-clear peligroso) **sigue** exigiendo TTY / confirmación exacta; no se relaja. Verificable: los tests existentes de admin siguen verdes.
- **SC-007**: audit `run_reaped` emitido antes de la mutación (audit-before-act); si el append falla, **no** se repea (fail-closed). Verificable: unittest con audit que lanza.
- **SC-008**: suite completa verde (ejercitada offline, sin tocar 8765/2302/procesos globales) incluyendo los nuevos tests. Verificable: `unittest discover`.
- **SC-009** (F-07, incluido): las tres rutas →`UNRECONCILED` de `stop_run` limpian `owner_session_id`/`owner_lease_id` (espejo de `recover_after_restart`). Verificable: unittest.

## Scope — Out of scope *(mandatory)*

- **No** se elimina ni se relaja el guard de kill (`process-guard.ps1`) ni el force-clear TTY de admin (F-05 se atiende **añadiendo** una vía segura, no abriendo la peligrosa).
- **No** se toca Enforce/PBO/`MCP_BRIDGE_VERSION`.
- **No** se arregla el crash de CE/`storage_1` (ajeno al MCP; el mod y el server los gestiona el usuario).
- **No** se implementa multi-instancia de DayZ ni se cambia la invariante one-run.
- **No** se aborda el P1 F-01 (reclaim embedded) — es un fix separado listado en la auditoría.
- **No** se poda el histórico de runs `EXITED` (F-03/C-03) — cosmético, aparte.

## Assumptions

- **ASSUMED**: `REAP_INTERVAL_S = 30.0` s por defecto (tunable). Decide latencia de auto-heal, no correctitud → **diferido** (no bloquea; el valor se ajusta sin cambiar el contrato). Con el `stop` Event del daemon para parada limpia.
- **ASSUMED**: estados reapables = `{RUNNING, RUNNING_IDLE, UNRECONCILED}`. `STARTING`/`STOPPING` (operación en curso) se dejan a `recover_after_restart`; `EXITED` es terminal. Decide alcance; **resuelto** por diseño (los tres son los estados que retienen ProcessRecords sin operación activa; el reaper corre bajo `_operation_lock` así que no puede coincidir con un start/stop en vuelo).
- **ASSUMED**: el reaper gatea en diag-limpio vía `_diag_snapshot_registered(set())` (no reapear si hay un DayZDiag no atribuido). Bajo la invariante one-run, mientras el ghost bloquea no puede haber otro run activo → sin DayZDiag → diag limpio → repa. **Resuelto** por diseño (fail-closed: diag desconocido → no repea).
- **ASSUMED**: `guard.snapshot(dead_pid)` → `{"error":"process_not_found","exit_code":4}` (de `process-guard.ps1:44,110`), ya consumido por `_reconcile_survivors:1173-1178` en código in-game-probado. Verificado por reuso.

## Forward Contract (R8-extended)

| Consumer | Symbol it reads | Kind | Verify status |
|---|---|---|---|
| reaper (nuevo) | `ProcessLifecycle._reconcile_survivors(processes)->(survivors,error,status)` | método (muerto=skip exit4 / mismatch=error / guard_unavailable=error) | `process_lifecycle.py:1164-1185` |
| reaper | `ProcessLifecycle._diag_snapshot_registered(pids)->(ok,reason)` | método | `process_lifecycle.py:1187-1211` |
| reaper | `ProcessLifecycle._audit(event,client,reason,decision,**extra)` (client acepta `None`) | método | `process_lifecycle.py:379-388` |
| reaper | `ProcessLifecycle._operation_lock` (`threading.RLock`) | lock | `process_lifecycle.py:366` |
| reaper | `ProcessLifecycle._quarantined()` | método (retail gate) | `process_lifecycle.py:394-406` (usado `:873`) |
| reaper | `RunManifestStore.list_runs()` / `.replace(run)` | manifiesto | `process_lifecycle.py:170` / usado `:899,957,1140` |
| reaper | `RunRecord` invariante EXITED⇒owner None + processes [] | dataclass | `process_lifecycle.py:110-113` |
| reaper | `_ACTIVE_STATES` = `RUN_STATES-{EXITED}` | constante | `process_lifecycle.py:22-24` |
| daemon tick | `install_idle_watchdog(idle_seconds,timeout_s,on_idle,*,poll_interval,sleep,stop)` (patrón thread + `stop` Event) | helper | `orphan_guard.py:860-896` |
| daemon tick | `run_daemon` `stop=threading.Event()` + activación tras bind | wiring | `daemon.py:282-318` |
| daemon tick | `state.lifecycle` (attach) | atributo | `daemon.py:188` |
| agent-callable (B) | `@app.tool` registro FastMCP | patrón | `server.py:688-716` |
| agent-callable (B) | `/lifecycle/*` rutas + `_handle_lifecycle` | HTTP | `loopback.py:59-64,1166-1194` |
| agent-callable (B) | `ProcessLifecycle._authorize` / `_authority` (lease gate lifecycle) | métodos | `process_lifecycle.py:409,467` (usado en start/stop/adopt) |
| se preserva | `admin_reconcile` (force-clear peligroso, TTY) | método | `process_lifecycle.py:1229` / `admin_cli.py:61` / `loopback.py:1196-1239` |
| F-07 | rutas →UNRECONCILED de `stop_run` | edición | `process_lifecycle.py:897,975,1013` |

## Verification plan

| Criterion | Verification | Where |
|---|---|---|
| SC-001..003, 005, 007, 009 | unittest con FakeGuard (snapshots inyectados: dead/mismatch/alive), fake audit que lanza, fake diag | offline |
| SC-004 | grep `terminate\|Kill\|kill_pid` en la ruta del reaper (0) + FakeGuard `terminated` contador == 0 | offline |
| SC-006 | los tests existentes de admin/TTY siguen verdes | offline (regresión) |
| SC-008 | `.venv-mcp\Scripts\python.exe -m unittest discover -s tests -t .` (aislado: puertos efímeros, TemporaryDirectory, mocks; sin 8765/2302) | offline |
| Scenario 1-4 | gate in-game batched: run gestionado → matar DayZ por fuera → observar reap auto + agent-callable | in-game (R5, opcional — el offline cubre el contrato; el in-game confirma el snapshot real del guard sobre PIDs muertos) |
| Data-critical | `rigorous-data-audit` (R9) sobre reaper + reconcile no-TTY antes de release-safe | gate R9 |

## Open questions / NEEDS CLARIFICATION

- Ninguna que decida correctitud. `REAP_INTERVAL_S` y la exposición exacta de (B) (tool MCP `lifecycle_reap` vs subcomando `lifecycle_cli reap`) son de diseño, no de contrato; implemento ambos con el mismo predicado de seguridad (all-dead) y lo confirmo en el analyze gate.
