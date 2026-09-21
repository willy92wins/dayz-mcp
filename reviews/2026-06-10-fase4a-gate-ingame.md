# Gate 4A — wrapper MCP stdio — in-game PASS (2026-06-10)

> Conduce: Claude. Plan: `plans/2026-06-10-fase4-mcp.md` §8-4A. Implementación auditada:
> Codex (receptor PASS, ver inbox). **Veredicto: GATE=PASS** — smoke 9/9 + regresión
> back-compat GATE=PASS + in-game 8/8, con 2 findings de producto cazados y corregidos
> durante el gate (GATE4A-001/002).

## Resultado por punto del plan (§8-4A)

| # | Criterio | Evidencia | Estado |
|---|---|---|---|
| 1 | E1 parcial: `tools/list` = 9 tools + `query_player_state` pos real + cliente MCP real | smoke `tools_list_9` (las 9 exactas, sin `vehicle_drive`/`exec_enforce`); ingame `query_player_state_ok` → `pos [6072.04, 6.66, 1940.37]` del player vivo; `claude mcp list` → `dayz-mcp: √ Connected` (handshake real de Claude Code) | ✓ |
| 2 | Visual end-to-end | `camera_set` (orient+roll) → `capture_screenshot` → PNG 239px real (casa Chernarus, verificado visualmente) `b64len 61916 ≈ 21.2k tokens ≤ 25k` → `camera_get` ok | ✓ |
| 3 | E4 concurrencia | lock de instancia: 2ª instancia con cliente real muere (initialize falla, EADDRINUSE en stderr) — **tras fix GATE4A-002**; 2 `camera_set` paralelas → ambas ok, serializadas por el mutex (unit-test determinista + in-game sin corrupción) | ✓ |
| 4 | Resiliencia | smoke: timeout 2s → `ToolError` "server peer has never polled; queue_depth=1; version_state=legacy"; ingame: tras 10s de 401s (stale key) el bridge recupera en **6.0s** medidos con la key buena | ✓ |
| 5 | Regresión back-compat | `run-fase3.ps1 -NoStop` con el shim refactorizado → `FASE3_VERDICT PASS` (6/6: D1 matrix 3.4e-05 + D2 captura budget/nonblack/sync) + fail-closed 401/400 intactos; unittests existentes PASS con python del sistema SIN mcp (stdlib-only real) | ✓ |
| 6 | E3 install end-to-end | `install-mcp.ps1` print-only → venv + pin + keyfile + samples + comando registro; **tras fix GATE4A-001**; registro real `claude mcp add` ejecutado | ✓ |
| 7 | Negativos | (a) `world_spawn` sin `type` → isError validación pydantic; `camera_set` matriz inválida → isError `bad_args`; (b) `max_tokens=1` → downscale al límite (92 b64 chars, rama válida del criterio); (c) keyfile stale → `ToolError` + liveness null + `queue_depth` visible; (d) shutdown → puerto reusable | ✓ |

## Findings del gate (producto) — corregidos in-situ

- **GATE4A-001 (E3)**: `requirements-mcp.txt` no incluía **Pillow** (lo necesita `mcp_capture`,
  importado por `server.py`) → el venv limpio no podía ni cargar el server. Fix: pin
  `Pillow==12.2.0` (la versión del sistema validada en D2). Cazado por el punto 6 del gate.
- **GATE4A-002 (E4)**: `http.server.HTTPServer` setea `allow_reuse_address=1` y en Windows
  `SO_REUSEADDR` permite a un 2º proceso hacer LISTEN del puerto activo → el "bind exclusivo"
  del lock de instancia NO existía (dos servers se repartirían los polls del bridge en
  silencio). Fix: `ExclusiveThreadingHTTPServer(allow_reuse_address=False)` en
  `dayz_mcp/loopback.py` + test de regresión nuevo `tests/test_instance_lock.py`. Suite 19/19.
  La suite de Codex no tenía test del lock — por eso se escapó al receptor.

## Fixes del harness del gate (no producto)

- Lock-test: una 2ª instancia stdio "pelada" (sin cliente) espera initialize para siempre
  (el lifespan corre al abrir sesión) → el test correcto usa un 2º cliente MCP real.
- Recovery-test: sleep fijo de 3s < backoff real del bridge tras 401s sostenidos → polling
  con deadline 45s; recovery real medido = 6.0s.

## Evidencia

- `tools/_fase4a/gate4a-smoke-verdict.json` (9/9, overall_pass=true)
- `tools/_fase4a/gate4a-ingame-verdict.json` (8/8, overall_pass=true)
- `tools/_fase4a/gate4a-capture-evidence.png` (frame real vía tool MCP)
- `tools/_fase4a/regression-run.log` (run-fase3 GATE=PASS, workroot `_fase3/run_20260610_182002`)
- Harness reutilizable: `tools/gate4a_mcp_client.py` (modos smoke/ingame; sirve para re-cert y
  como base del gate 4B)

## Notas operativas

- Registro Claude Code quedó en scope **local** del project `...\DayZ_MCP_dev\tools` (el cwd
  al registrar). Para usar las tools desde sesiones en otro cwd: re-registrar con `-s user` o
  desde el cwd deseado. Las tools estarán disponibles interactivamente en la PRÓXIMA sesión.
- `mcp==1.27.2` desinstalado del user site (finding receptor): el constraint stdlib-only del
  shim quedó observable y verificado.
- `EXPECTED_BRIDGE_VERSION="4"` (`dayz_mcp/server.py:20`): la constante Enforce de 4B debe
  emitir exactamente ese valor en `ver=`.

## Pendiente (NO de 4A)

Tramo 4B (handoff aparte): `world_time_set` + `world_weather_set` + `exec_enforce` +
version-report `ver=` en ambos pollers (1 rebuild del PBO) → gate 4B (plan §8-4B, incluye
los 4 casos del handshake y los negativos 4B.7).
