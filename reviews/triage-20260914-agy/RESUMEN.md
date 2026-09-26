# Triaje del buzón DayZ-MCP — pasada 2026-09-14 (agy Gemini 3.8 + revisión Claude)

- Universo: `pipeline_inbox` sin resolver = **59** (`feedback.jsonl` 1132 filas: 579 entradas, 522 ids resueltos). `triage.jsonl` no existe.
- Snapshot: `git archive origin/main` **f269b1c** (576 ficheros). Todos los `path:line` son relativos a ese snapshot.
- Proponente: `gemini-3.8-flash-high` × `agy` 1.2.2, 6 lanes temáticas, brief común sha256 `098b7a0d…`. 59/59 filas, 0 citas fuera de fichero/rango, snapshot intacto por manifiesto sha256, 0 ficheros en el scratch de agy.
- Incidencias de transporte: G3 intento 1 `500 INTERNAL` sin respuesta (reintento limpio); G1a, G1b, G2, G4, G5 con `status:ERROR` + `500 INTERNAL` **tras** entregar la respuesta completa (contenido aceptado).
- Revisor: Claude (Opus 5, otra familia). Columna «Gemini» = propuesta; columna «Decisión» = revisión con lectura directa.
- Consumo agy (entrada): G1a 388 k · G1b 499 k · G2 609 k · G3 401 k (+62 k fallido) · G4 307 k · G5 241 k.

## A1 — Cerrable ya: verificado y en servicio (13)

| ficha | proyecto | Gemini | Decisión | Evidencia |
|---|---|---|---|---|
| ae65 | DayZ_MCP | X-YA_RESUELTO | cerrar | PRs #40/#41; `native_launcher_backend.py` vivo == main (blob 706351d); vivo `dayz_test_run(build=true)` ok 11,35 s, run `484fa24d` (`%TEMP%\p35-p2-1-dump\dayz-test-run.json`); `server.py:405`, `test_mcp_tools.py:328` |
| 49d0 | LFPowerGrid | X-YA_RESUELTO | cerrar | misma evidencia; `native_launcher_backend.py:1559`, `test_native_launcher_backend.py:1618` |
| a429 | DayZ_MCP | X-YA_RESUELTO | cerrar | commit 80ed00b en el árbol vivo; campos del overlay observados hoy en `bridge_status`/`session_status` vivos (`available_for`, `daemon_modules`, `tool_registry_remediation`, `mutation_rejects_meta`); `tools/README-mcp.md:193`; PR #29 |
| 0ab2 | DayZ_MCP | X-YA_RESUELTO | cerrar (estaba PARK: confirmar) | edc7bb3 en árbol vivo, `session_coordination.py:16` `LEASE_GRACE_S = 90.0`; R9 offline PR #31; occupancy PR #33; H8 vivo `2e5e74fe` (cierre 14-sep; recibo del store Cursor no presente en disco) |
| 546d | DayZ_MCP | X-YA_RESUELTO | cerrar; G3 GREEN al lote in-game | PR #26 (= HEAD vivo d2f9dad); I3 survive-kill vivo `66330022` rows==106 (cierre 14-sep); `MCP_CarScript.c:386`, `vehicle_trace.py:18` |
| b256 | LFPowerGrid | X-YA_RESUELTO | cerrar | PR #18; `server.py:4794` `inventory_attach` |
| c82e | LFPowerGrid | X-YA_RESUELTO | cerrar | `server.py:5763` `entity_state` en main y en HEAD vivo (git grep 12/12), desde e98a496 (09-09) |
| 4f83 | DayZ_MCP | X-YA_RESUELTO | cerrar | `camera_restore.py:17-43` tricotomía released/still_active/unverified («absence is never success»); PR #22; B3 #4b cerrado in-game (HANDOFF 12-sep) |
| 0d65 | DayZ_MCP | X-YA_RESUELTO | cerrar | `camera_restore.py:38` view=player → released; PR #22 |
| 8f76 | ForzaDayZ | X-YA_RESUELTO | cerrar (por diseño) | sin foco forzado + `frame_stale`: `server.py:5216`, `dayz_test_tool.py:622`, `mcp_capture.py:1165` |
| f5a7 | Arma2Quad | X-YA_RESUELTO | cerrar | PR #16 pts 1/2/4 (`test_lote_msgs_f5a7.py:1`); cámara por PR #22 |
| 5cca | — | X-REROUTE | **corregido → cerrar como documentado en producto** | receta L5/L6 en la descripción de `camera_get`, `server.py:5119-5124` |
| 19e1 | LFQuad3 | X-REROUTE | **corregido → cubierto en producto** | camino rápido del pid en `steam_preflight.py:495-545` (el propio reporte lo usó para arreglarlo); receta manual corregida en skill `dayz-test-ingame` (sesión LFQuad3 12-sep) |

## A2 — Resuelto en main, NO en servicio (3) — cerrar tras promover main al vivo

| ficha | proyecto | Gemini | Decisión | Evidencia |
|---|---|---|---|---|
| 2c43 | DayZ_MCP | X-YA_RESUELTO | cerrar tras promoción | PR #32; `server.py:5826`, `inbox.py:16`, `test_pipeline_feedback.py:400`; `inbox.py`/`server.py` vivos sin e2b645b/e022ed0 |
| b1ff | Arma2Quad | X-YA_RESUELTO | cerrar tras promoción | PR #34; `server.py:817`; vivo sin daf9064/9eafe7a/a3438ce |
| 14de | Arma2Quad | X-YA_RESUELTO | cerrar tras promoción | PRs #36/#42; `vehicle_trace.py:378`; vivo 190/215 medido con cliente de worktree (`48413dcd`); `vehicle_trace.py` vivo sin b28fcf3/00391d3 |

## A3 — No es producto DayZ-MCP (21) — cerrar con puntero

| ficha | proyecto | Gemini | Decisión | Destino / evidencia |
|---|---|---|---|---|
| a997 | LFPowerGrid | X-REROUTE | cerrar | arnés Claude Code; `memory/bash-tool-single-quote-wrapper.md`; no reproduce hoy (sesión 741dc585: decenas de Bash con comillas simples OK) |
| 364c | — | X-DUPLICADO a997 | cerrar | duplicado de a997 |
| de18 | LFQuad3 | X-REROUTE | cerrar | arnés; `bash-tool-single-quote-wrapper.md` + `powershell-harness-guard-removeitem.md` |
| 4f47 | LFPowerGrid | X-REROUTE | cerrar | arnés; notas `powershell-*` de memoria (existen; no leídas enteras) |
| 55df | LFPowerGrid | X-DUPLICADO 4f47 | cerrar | cita 4f47 en el cuerpo |
| a622 | Arma2Quad | X-REROUTE | cerrar | arnés; mismas notas |
| 4b26 | — | X-REROUTE | cerrar + anotar lección | PowerShell `$f`/`$F`: grep en memoria sin resultado → falta nota |
| 1b06 | LFPowerGrid | X-REROUTE | cerrar | `cmd-from-cmd-without-call-never-returns.md`, `host-config-trees-now-versioned.md` (autocrlf) |
| 6391 | LFPowerGrid | X-REROUTE | cerrar | `compact-kills-background-tasks.md` + skill `delegar` §vigilante |
| 2503 | LFSecure | X-REROUTE (DUDOSA) | **cerrar (verificado)** | es otro servidor MCP (`mcp__dayz_mods__write_file`); 0 hits `dayz_mods` en `tools/dayz_mcp`; sesión 10-sep lo dice |
| 1e79 | LFQuad3 | X-REROUTE | cerrar | DayZ Tools (binarize) |
| 9678 | AssetLab | X-REROUTE | cerrar | AssetLab / Blender |
| 8c72 | SecretRock | X-REROUTE | cerrar | SecretRock; hay además `fb-20260914-111147-8c72.md` suelto en la carpeta del buzón |
| 23f1 | LFSecure | X-REROUTE | cerrar | LFSecure V2 |
| bb5f | LFSecure | X-DUPLICADO 23f1 | cerrar | LFSecure V2 |
| 7fc3 | LFPowerGrid | X-REROUTE | cerrar | `build_isolated_pbo` no existe en el repo (grep 0) |
| af59 | LFQuad2 | X-REROUTE | cerrar | scripts `dayz-test.ps1` de los mods: `reports/2026-09-07-af59-dayz-test-convergencia.md:251` |
| 8724 | DayZ_MCP | X-REROUTE (anexo af59) | cerrar | mismo destino |
| d688 | LFPowerGrid | X-REROUTE | cerrar | documentado `tools/README-mcp.md:94` |
| 135d | LFPowerGrid | X-REROUTE (DUDOSA) | cerrar | Enforce del mod (init.c / 5_Mission) |
| e6c1 | LFQuad3 | X-REROUTE | cerrar | lección LL-307 a runbook/skills; relacionado con 2223 |

## B — Abiertas, implementables sin juego (5)

| ficha | proyecto | Gemini | Decisión | Qué queda |
|---|---|---|---|---|
| 050e | LFQuad2 | X-YA_RESUELTO | **corregido → parcial, C2** | petición 1 hecha (H14 PRs #30/#39/#43; D4 vivo `62b8e0ce`). Quedan 2) el rechazo dice desde cuándo (registro / regeneración del daemon) y 3) `dayz_test_run` avisa si el registro del llamante ya está stale antes de lanzar |
| 7ef2 | LFPowerGrid | CAMBIO C2 | C2 (cuidado R7/R8) | distinguir run de otra generación vs inexistente en stop; trazar huérfanos en `session_status` (`dayz_test_tool.py:1800`, `process_lifecycle.py:289`) |
| dff2 | LFSecure | EVIDENCIA C1 | EVIDENCIA + docs C1 | audit log: `session_release_finished` con `runs_released` y **sin** `lifecycle_stop_outcome`; `run_reaped all_processes_gone` 00:43:41Z → release no mató. Documentar RUNNING_IDLE en `session_release` y runbook; causa real de la muerte sin investigar (RPT server LFPowerGrid 00:30–00:43Z) |
| 145c | DayZ_MCP | X-DUPLICADO 2223 | **corregido → anexo de 2223** | docs C1 ya: `dayz_test_run` bloquea hasta `wait_for_box_s` + lanzamiento; cliente con timeout corto usa valor menor. Resto con 2223 |
| c12b | DayZ_MCP | X-ATERRIZAR | **corregido → docs C1** | DayZDiag sin foco ~20 fps: anotar en captura/bancos |

## C — Abiertas que exigen juego (10) — lote in-game único

| ficha | proyecto | Gemini | Decisión | Qué queda |
|---|---|---|---|---|
| deb9 | DayZ_MCP | CAMBIO C3 | C3 (Enforce) | histéresis/cooldown de ShiftUp en `MCP_CarScript.c:742` |
| 07a1 | DayZ_MCP | CAMBIO C3 | C3 (Enforce) | elegir el coche más cercano a `pos` / `expected_type`: `MCPClientBridge.c:2795`, `server.py:5352` |
| f47b | Arma2Quad | CAMBIO C3 | C3 investigación | render congelado tras `ReleaseCamera` (`MCPClientBridge.c:4346`) |
| f298 | DayZ_MCP | X-YA_RESUELTO | **corregido → verificar in-game** | `ReleaseGameFocus` en `MCPClientBridge.c:4364-4380`, llamado en `MissionGameplay.c:16`, PBO vivo == main |
| 3bb4 | DayZ_MCP | X-YA_RESUELTO | **corregido → B3 #6** | `key_press(dik=1)` frente al menú (plan tres bloques :202, :343); `MCPClientBridge.c:865` |
| 5dbe | DayZ_MCP | X-YA_RESUELTO | **corregido → B3 #6** | ensayo vivo lease→reciclo (`session_handoff.py`; plan :273) |
| 1f21 | DayZ_MCP | CAMBIO C3 | C3 | calibración viva (B3 #6) + campos de diagnóstico |
| 6d18 | SUB_BRZ | CAMBIO C3 | parte C2 offline + decisión | `session_locked` en `mcp_capture.py:862`; captura engine = decisión (MakeScreenshot roto, T165276) |
| 311d | Arma2Quad | CAMBIO C3 | **siguiente paso rechazado** (contradice D-68) | PR #35 Python-only honesto; P2-1 imposible sin getter → decisión |
| 8bc6 | DayZ_MCP | CAMBIO C3 | decisión + C3 | borrar por radio/tipo vs no persistir spawns (`MCPBridge.c:636`) |

## D — Decisión del dueño, PARO o PARK (7)

| ficha | proyecto | Gemini | Decisión |
|---|---|---|---|
| 2223 | DayZ_MCP | CAMBIO C3 | modelo de cola durable (queue_offer / on_busy / box_enqueue / CLI); absorbe 145c |
| 8604 | LFPowerGrid | CAMBIO C3 | contrato de parada limpia (`server.py:4140`, `native_process_guard.py:194`) |
| 2edd | DayZ_MCP | X-YA_RESUELTO | **corregido → parcial**: 2edd-2 hecho (PR #20, `dayz_test_tool.py:1860` `stop_method=forced_kill`); 2edd-1 PARK; parada limpia = 8604 |
| dae1 | DayZ_MCP | CAMBIO C2 | dae1-3 hecho (PR #20); dae1-1 y dae1-2 PARK (worker sellado) |
| 3fc1 | LFPowerGrid | CAMBIO C3 | PARO (HANDOFF: no reabrir sin ángulo nuevo) |
| dce1 | DayZ_MCP | CAMBIO C3 | PARO (hereda de 3fc1) |
| 1025 | DayZ_MCP | CAMBIO C2 | PARO |

## Correcciones del revisor a Gemini (10)
`050e`, `2edd` (no resueltas del todo) · `3bb4`, `5dbe`, `f298` (falta verificación viva) · `19e1`, `5cca` (sí producto, ya cubiertas) · `311d` (siguiente paso contradice D-68) · `145c` (anexo, no duplicado) · `c12b` (tipo X-ATERRIZAR mal aplicado).

## No verificado
In-game de nada · suite completa sobre f269b1c · recibos del store Cursor citados por el cierre del 14-sep que no están en disco (`internal/mcp-h8-w7b.md`, `mcp-i3-survive-kill.md`) · causa de la muerte del run de `dff2`.
