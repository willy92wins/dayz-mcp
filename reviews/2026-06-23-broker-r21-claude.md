# R21 adversarial — Broker/daemon (DayZ-MCP multi-sesión)

> **Tipo**: R21 estructural de código infra/tooling (NO R9 — el broker no toca progreso de
> jugador; es Python-only, Enforce/PBO intactos). Reviewer: Claude (Cowork). Fecha: 2026-06-23.
> **Veredicto: APROBADO CON FIXES APLICADOS.** 2 P1 + 1 P2 de seguridad-lifecycle corregidos
> in-place con test; suite **120/120** (era 113) + E2E binario real **5/5**. Resto al backlog
> (BUG-034..039), threat-model-bounded o gated al gate in-vivo. Embedded/game/shim byte-estable.

## Alcance y método

Código revisado (broker, post-D-14): `dayz_mcp/{core,daemon,server,loopback,orphan_guard}.py`,
tests `tests/test_{daemon,client_mode}.py`, E2E `tools/_broker/e2e_daemon.py`.

Método: re-verificación offline desde cero (suite 113/113 + E2E 5/5) → **7 subagentes
adversariales en paralelo, uno por ángulo** (a-g del prompt), read-only e independientes,
briefados con las invariantes cerradas (LL-156) para no marcar diseño intencional como bug →
**verificación propia de cada finding contra el código** (R2 cite-then-verify / pre-output
Pattern 2) antes de aplicar → fixes in-place + test → re-suite.

Sobre el `Exception in thread serve_forever` en stderr de la suite: **ruido de teardown
confirmado, NO thread huérfano**. Reproducido 1/3 corridas, siempre con `OK / 113 tests /
exit 0`. Es la race Windows `server_close()` ↔ `select()` del loop; el hilo es `(serve_forever)`
del path pre-existente `LoopbackServer` (el helper de test `DaemonHttpServer` ya la traga,
`test_daemon.py:70-76`), `daemon=True` → no sobrevive al proceso.

## Findings por severidad

### P1 — corregidos (cluster de un mismo root cause)

El discriminador de reclaim del daemon (`probe_status_healthy` → `False`) **conflaba tres
estados distintos**: (1) conexión rechazada = muerto/nada → kill-eligible; (2) bindeado-pero-aún-
sin-servir; (3) 401 key foránea = vivo-no-mío; (4) ocupado >timeout. Trataba 2/3/4 como
kill-eligible.

**A-1 — el daemon perdedor mata al ganador SANO en la ventana bind→serve_forever** (high).
`daemon.py:144-150` + `_bind_with_reclaim:104-113` + `orphan_guard.py:678`. Dos daemons spawnean
a la vez; A gana el bind exclusivo (socket bindeado+listening) pero su hilo `serve_forever` aún
no acepta; B (EADDRINUSE) prueba `/status` → la conexión TCP entra al backlog pero no hay
respuesta HTTP → timeout → `probe_status_healthy=False` → B reclama y **mata a A** (python +
`-m dayz_mcp` + listening, sin guarda de self-race ni de edad del listener). Verificado
empíricamente por el subagente (socket bindeado-sin-servir devuelve `TimeoutError` → False;
con `serve_forever` arrancado responde en ~1 ms).

**B-1 — key mismatch mata un daemon SANO** (high). `orphan_guard.py:646-648` + `daemon.py:104-113`.
Daemon X sano con key K1. Sesión/daemon con K2≠K1: `probe_status_healthy(port,K2)` → X responde
**401** → `HTTPError` → `False` → reclaim con `is_healthy` (K2) → 401 → False → X es
python+`-m dayz_mcp`+listening → **matado**, pese a estar sirviendo a otras sesiones en K1.
Alcanzable sin setups exóticos: regenerar el keyfile mientras un daemon viejo sigue vivo
(los daemons perviven hasta el idle de 1800s), o un 2º clon/instalación con su propio
`.dayz_mcp.key` en el mismo `:8765`. El re-run normal de install reusa la key
(`install-mcp.ps1:67-74`), lo que baja la probabilidad pero no la elimina.

**B-2 — TOCTOU: un daemon ocupado >1.0s es matado tras recuperarse** (high→P2). `orphan_guard.py:678`
→ `682-702`. La salud se chequea UNA vez (timeout 1.0s) y luego corren netstat + wmic/powershell
(timeouts 15s/20s) antes del kill, sin re-probe. Un daemon legítimamente lento (round-trip
Enforce en vuelo, `/poll` con `time.sleep(delay_ms)` hasta 5s, GC, host cargado) se mata tras
volver a estar sano.

**Fix aplicado (A-1 + B-1 + B-2)**:
- `orphan_guard.probe_listener_responsive(port)` **(nuevo)**: key-agnóstico — cualquier respuesta
  HTTP (incl. 401) = servidor VIVO → no reclamar; solo refused/timeout/no-HTTP = no-responsivo.
  Distingue B-1 (foráneo responde 401).
- `try_reclaim_unresponsive_listener`: re-probe **sostenido** (`is_healthy() or is_responsive()`
  a lo largo de `probe_attempts=3` × `probe_interval=0.5s`); solo un holder que falla TODOS los
  intentos se reclama. Un ganador en arranque (A-1) o un daemon ocupado (B-2) flipea a vivo
  dentro de la ventana. `is_responsive`/`sleep`/attempts inyectables para test.
- `daemon._bind_with_reclaim`: si el reclaim no procede (holder vivo/foráneo/no identificable) el
  daemon perdedor **pierde la carrera limpiamente (return None → exit 0)**, ya no re-lanza
  EADDRINUSE (no crash).
- Tests: `test_responsive_foreign_key_holder_is_preserved` (B-1),
  `test_holder_that_becomes_responsive_mid_window_is_preserved` (A-1),
  `test_responsive_probe_treats_foreign_key_as_alive` / `_false_when_no_listener` (probe).
  Los 4 tests previos del reclaim ahora inyectan `is_responsive`/`sleep` (herméticos, rápidos).

> No viola LL-156: el discriminador del daemon sigue siendo HEALTH/responsiveness (no parentesco),
> y el reclaim por-ancestro del embedded (`try_reclaim_port`/`should_reclaim_listener`, C1) NO se
> tocó. Solo se endureció el lado health-gated para no confundir "vivo" con "huérfano muerto".

### P2/P3 — corregidos (cheap, claros)

**F-1 — `exec_enforce` quema id + escribe audit "allowed" fantasma con cola llena** (P3, high).
`loopback.py:136-156`. La rama exec reservaba id + auditaba "allowed" ANTES de comprobar
capacidad → un 429 dejaba una entrada "allowed" para un comando que nunca corrió (el ledger del
chokepoint de seguridad sobre-reporta). **Fix**: reservar slot+id bajo lock comprobando capacidad
ANTES del audit (append re-chequea capacidad para la ventana estrecha del write). Test
`test_exec_queue_full_rejects_before_allowed_audit`.

**C-2 — respuesta 200 malformada del daemon escapa como `KeyError`/`JSONDecodeError` crudo** (P2,
high). `server.py:280,314`. Un 200 sin `id` o con body no-JSON crasheaba la tool. **Fix**:
`ClientRuntime._decode_body` (no-JSON / no-dict → `ToolError("daemon_bad_body")`) + guarda
`"id" not in payload → ToolError("daemon_bad_enqueue_response")`. Tests
`test_malformed_enqueue_response_is_tool_error` + `test_decode_body_rejects_non_json_and_non_object`.

**D-1/D-2 — supervivencia detached opaca + comentario sobre-promete** (P2, high). `daemon.py`.
`spawn_detached` no logueaba qué rama tomó (breakaway OK vs job-bound fallback), y el docstring
afirmaba "self-heals via re-spawn" como si recuperase multi-sesión. **Fix**: log por rama
(`broke away from the job` / `CREATE_BREAKAWAY_FROM_JOB denied; daemon is job-bound...` /
`launched job-bound`) + docstring honesto sobre la degradación. Hace el **gate in-vivo
diagnosticable en una línea** (ver abajo). Sin test (solo logging/doc).

### Backlog (ledgerizado — fuera de alcance del fix de esta sesión)

| ID | Sev | Finding | Por qué al backlog |
|---|---|---|---|
| BUG-035 | corruption (integ/sec) | **E-1**: el version-gate lee `_poll_versions[peer]`, escribible por cualquier key-holder vía `/poll?peer=server&ver=<ok>` → un cliente podría desbloquear el gate sin el juego real | threat-model single-user/local/shared-key: el mismo actor ya tiene acceso a comandos; integridad, no boundary break. Hardening = separar game-key de session-key |
| BUG-036 | degradation | **F-2**: un peer con versión bloqueada wedgea su cola a `MAX_QUEUE=64` → 429/409 hasta que la versión se recupere | self-heals con un poll de versión buena; sin pérdida (cliente recibe error limpio) |
| BUG-037 | degradation | **C-1**: el timeout de tool no se honra (~12s medidos) cuando el daemon está inalcanzable, porque `_ensure_daemon` poll-ea 10s dentro de un `_call` | solo en estado ya degradado (daemon caído); falla limpio. Fix = acotar el poll por el deadline restante |
| BUG-038 | degradation (lifecycle) | **A-2** spawn storm cross-sesión (`_spawn_lock` per-proceso) + gaps de test **A-4** (race de discovery) / **D-3** (supervivencia Job-Object) | convergencia daemon-side por diseño (LL-156), mitigada por FIX1; Job-Object = gate in-vivo |
| BUG-039 | cosmetic (maint) | **D-5/E-2**: `--exec-audit-path` no se forwardea al daemon spawneado (campo `ServerConfig` existe, sin flag CLI) | latente: hoy sin impacto (no hay flag); si se añade, forwardear en `build_daemon_argv` |

### Confirmados SANOS (no-bug)

- **(e) Fail-closed preservado en el split**: whitelist/version-gate/exec-chokepoint viven en el
  daemon (`enqueue_command`/`_enqueue_exec_enforce`/`record_poll`), idénticos para embedded y
  cliente (misma `ServerState`). El cliente solo proxya. `exec_enforce` daemon-autoritativo
  (whitelist solo con `enable_exec_enforce` del DAEMON; off→400, allowlist None→403). Todo
  endpoint tras `_authorized` (hmac constant-time). La key **nunca** a log ni a command line
  (`build_daemon_argv` forwardea `--keyfile` path, no `--key`; `log_message` no-op; URLs no
  logueadas). `/status` sin secretos.
- **(f) Concurrencia sin lease**: ids únicos bajo `_lock` (32k ids, 4 hilos, 0 colisión — probe
  empírico del subagente); resultados keyed por id, 0 mis-delivery en 4k roundtrips interleaved;
  todo campo de `ServerState` accedido bajo `_lock`. Cruce cámara/captura entre sesiones =
  interferencia lógica documentada (Fuera de alcance), SIN corrupción de estado del daemon.
- **(g) Embedded byte-estable**: con `version_validator`/`status_provider` None el 409 y el
  delivery-gate se saltan (idéntico a pre-broker); `/await` default `remove=0` (back-compat
  harness); `/poll`+`/result` (path del juego) no llaman `touch_client`; `mcp_server.py` shim y
  los 5 endpoints intactos; `core.py` extraído behavior-preserving; `EXPECTED_BRIDGE_VERSION="4"`
  == `MCPMessages.c` `MCP_BRIDGE_VERSION="4"`. 13/13 tests de regresión + 3 probes runtime.

## Gate in-vivo (lo único no decidible offline — lo corre el usuario)

La pregunta P1 no fabricable offline: **¿el daemon detached sobrevive a que Cowork cierre la
sesión spawner bajo su Job-Object?** (P5 del E2E solo cubre un spawner intermediario sin Job).
Con el fix D-1, el log del daemon spawner lo dice por rama. Pasos exactos en el HANDOFF y en el
mensaje de cierre de la sesión.

## Verificación

- Suite: `python -m unittest discover -s tests -t .` (`.venv-mcp`) → **120/120 OK** (113 + 7
  nuevos), exit 0.
- E2E binario real: `_broker/e2e_daemon.py` → **5/5 ALL_PASS**, exit 0, sin traceback.
- Scope: tocados `orphan_guard.py` (probe + reclaim), `daemon.py` (`_bind_with_reclaim`,
  `spawn_detached`), `loopback.py` (`_enqueue_exec_enforce` reorder), `server.py`
  (`_decode_body` + guarda enqueue) + 3 tests. NO tocado: Enforce/PBO, shim bare, `record_poll`,
  exec chokepoint semántico, gates 0-4/R21-X.5, reclaim por-ancestro del embedded (C1).
