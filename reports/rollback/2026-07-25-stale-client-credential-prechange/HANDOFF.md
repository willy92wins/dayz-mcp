# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
# DayZ_MCP — Estado vivo · snapshot 2026-07-24 (BUG-054/055)

**Última verificación:** launcher reconstruido tras drift de Steam y SUB_BRZ
validado en runtime. PE actual `4C677AB2…5653`; H11 server+client run
`71365b50-d6cc-4551-afe2-dcad8fc61c7b`; `world_spawn("SUB_BRZ")` devolvió
`ok=1`, `found=1`, `object_id=12`. Juego y objeto quedan abiertos.

## Estado actual
- BUG-054 cerrado operacionalmente: clausura 83/83 válida, rebuild reproducible
  ×3 y registro CAS actual `414CF840…AA58`.
- Server+client SUB_BRZ están gestionados en `RUNNING_IDLE`; ambos peers frescos.
- Coordinación final: owner/queue/self/pending/faults limpios.
- No se lanzó ni detuvo DayZ fuera de `dayz_test_run`; el mod SUB_BRZ no cambió.

## Issues abiertos
1. **[MEDIA] BUG-055** — `mode="all"` produce `readiness_failed` falso ×2 antes
   del timeout; causa discriminante no instrumentada. La composición tipada
   server→UDP/PID→client(run exacto) sí funciona.
2. **[BAJA] Tests de hash histórico** — focal nativo 78/80; dos asserts esperan
   un SHA de registro superseded, sin fallo del bundle actual.
3. **Utopia funcional / gate 2 Claude + 2 Codex / BUG-044 P1 legacy** — sin cambio.

## Próxima acción
Tratar BUG-055 como tarea separada: añadir razón tipada a cada rama `false` del
probe y un viability test bajo el worker nativo. No tocar el guard UDP/PID por
hipótesis. Para cerrar el run actual, usar `dayz_test_stop` con el ID exacto.

## Invariantes CERRADAS — NO retocar sin evidencia nueva
- Drift de miembro externo = fail-closed + rebuild reproducible + registro CAS;
  nunca relajar identidad/hash de la clausura.
- Lifecycle sólo por `run_id` exacto; no launch/kill manual ni runs ajenos.
- Recovery BUG-053 y token interno permanecen intactos.

## Punteros
- `AI/10_Projects/DayZ_MCP/research/2026-07-24-native-launcher-steam-drift-h11-readiness-codex.md`
- `AI/30_Sessions/2026-07-24-DayZ_MCP-SUB_BRZ-world-spawn-launcher-refresh.md`
- `AI/10_Projects/DayZ_MCP/bug-ledger.md` (BUG-054/055)

**Gate de arranque:** `Retomo DayZ_MCP desde BUG-054 cerrado / BUG-055 abierto: SUB_BRZ spawn PASS, run 71365b50 abierto`.
<!-- LIVE-STATE:END -->

---

## Log histórico

### 2026-06-28 — Captura cap-aware (MAX_MCP_OUTPUT_TOKENS) + WebP opt-in (ORTOGONAL a Fase 5)
- **Pregunta del usuario**: mejorar la parte visual del MCP. Diagnóstico: el pipeline ya estaba maduro (JPEG q82 default, crop, canal dual a disco `save_fullres`); el techo real = los ~25k tokens/respuesta de Claude Code. Research (agente claude-code-guide): `MAX_MCP_OUTPUT_TOKENS` SÍ sube ese techo (VERIFICADO docs/issues; default 25k; global a TODOS los MCP; `anthropic/maxResultSizeChars` NO aplica a imágenes); WebP aceptado pero con bugs 400 (#39146 mis-MIME, #15807).
- **Implementado** (Python puro, sin tocar Enforce/PBO): `mcp_capture.py` → `client_token_cap`/`default_max_tokens`/`resolve_request_budget` (margen `TOKEN_SAFETY_MARGIN=0.92`); `server.py capture_screenshot` clampa fail-closed antes de capturar + docstring con valores de referencia y semántica `scale`/`webp`; WebP en `encode_bytes`/`_mime_for` (opt-in); `SCALE_WIDTHS["full"]` 640→8192 (budget-limited).
- **Bug R8**: los unit-tests del budget pasaban pero `SCALE_WIDTHS["full"]=640` capaba el ancho ANTES del budget loop → el cap-raise no compraba resolución. Cazado por verificación funcional end-to-end (no solo unit), corregido + re-verificado.
- **Gates offline**: suite **143/143** (9 nuevos `tests/test_token_budget.py`); medición sobre frame real 1302×776 (jpeg q82, margen 0.92, `scale="full"`): 25k→581px · 50k→822px · 75k→1007px · 100k→1162px; `scale="small"`=512 hard-cap; clamp(200k@cap25k)=23k.
- **Config usuario**: `MAX_MCP_OUTPUT_TOKENS=75000` añadido al `settings.json` GLOBAL (env block; JSON validado, hooks/plugins preservados). Memoria `mcp-image-token-limit-claude-code` corregida (sin verificar → verificado + patrón server↔cliente + webp-brick + medición).
- **PENDIENTE = gate in-vivo** (no fabricable offline): sesión NUEVA → `scale="full"`/75k con el juego → confirmar imagen ~1010px sin rechazo del cliente; webp → vigilar 400-brick. Archivos: `tools/mcp_capture.py`, `tools/dayz_mcp/server.py`, `tools/tests/test_token_budget.py` (nuevo).

### 2026-06-14 — Fix captura visual fase-3 (grab stale) — root cause + fix + validación in-game
- **Síntoma** (observado 06-14 conduciendo el bridge por PowerShell): `camera_set` ok=1 pero los window-grabs salían BYTE-IDÉNTICOS entre launches distintos → la captura cogía un frame stale del escritorio (Steam/Cowork), no el render vivo.
- **ROOT CAUSE = el GRAB, no camera→render.** El grab viejo hacía `SetForegroundWindow()+CopyFromScreen(rect)`; desde un PowerShell de fondo el foreground-lock de Win32 ignora el request → CopyFromScreen leía lo compositado en esas coords. **camera→render SÍ funciona** (el bridge Enforce NO se tocó; SetActive(staticcamera) conduce el render).
- **Fix**: grab canónico único `tools/spike0/mcp-grab.ps1` con `PrintWindow(hwnd, PW_RENDERFULLCONTENT=2)` (superficie propia de la ventana, robusto a oclusión, sin robar foco) + fallback foreground-real (AttachThreadInput). Consumido por `mcp_capture.py` (tool MCP + fase3), `spike0-window-enum.ps1` y `A6 gate-mcp.ps1` (sin tocar el harness A6).
- **Content-aware gate (A2/A3)**: `mcp_client.py` `run_phase3_content` conduce lookat(player) vs lookat(cielo) y `phase3_capture_cases` añade `D2_capture_content` (delta_follow/delta_center/delta_live) → "D2 pasa" ahora significa que el visual funcionó de verdad. El check non-black viejo no distinguía live de stale.
- **Validación in-game** (4 launches): diagnóstico multi-método (`mcp-grab-diag.ps1`) + `run-fase3.ps1` GATE=PASS (`D2_capture_content` delta_follow=0.36, delta_live=0.0069, method=printwindow) + `gate-mcp.ps1` (player+arma encuadrado, `captures/mcp_probe_akm_i1.png`). Evidencia AKS74U: `tools/spike0/_grabdiag/subject_printwindow.png`.
- **Hallazgo readiness**: la captura puede coger un frame de carga/transición si se dispara antes de que el CLIENTE esté renderizando in-game (gate-i2 cogió la pantalla de carga; gate-i1 faithful cogió al player aún sin asentar). El content-aware lo caza (delta≈0). Requiere settle/readiness en el consumidor — ver fix `PHASE3_CONTENT_GRAB_SETTLE_S=2.0`.
- **Sin rebuild PBO, sin tocar Enforce, gates 0-2 intactos.** Suite venv 38/38. Ficheros: `mcp_capture.py`, `mcp_client.py`, `spike0/mcp-grab.ps1` (nuevo), `spike0/mcp-grab-diag.ps1`+`-analyze.py` (nuevos diag), `spike0/spike0-window-enum.ps1`.

### 2026-06-10 (noche, 3) — Fase 4B impl + GATE 4B in-game PASS → PROYECTO CERRADO
- Codex implementó 4B source-only (handoff propio): 3 handlers Enforce (`world_time_set`/`world_weather_set`/`exec_enforce`) + `ver=` en ambos pollers + `MCP_BRIDGE_VERSION="4"` + tools/gating Python. Receptor: scope limpio por mtime (8 handlers + MCPJobRunner intactos), 26/26.
- Gate 4B (conduce Claude): rebuild PBO 4B (AddonBuilder, verificado por strings) → regresión fase3 ×3 GATE=PASS → harness ingame4b **11/11 PASS**.
- **3 hallazgos del gate**: GATE4B-001 (bug producto, TODAS las tools: `ok is False` vs int 0/1 del bridge → errores de negocio como éxito; fix + fake alineado + test; LL-115), GATE4B-002 (allowlist BOM crashea arranque → utf-8-sig), GATE4B-LIM (limitación engine: `ExecuteEnforceScript` Developer-only devuelve false en server headless aun con wrapper vanilla EXACTO — probados 6 formatos; gating+audit verificados, ejecución documentada fuera de alcance, adjudicado con usuario).
- Fixes de harness del gate: sleeps fijos → polling con deadline (backoff del bridge); advisories (visible día/noche, exec engine) separados del gate; test_instance_lock bootstrap inline.
- Cierre: product-spec E1/E2 → ✓ (E3/E4 ya en gate 4A); Fuera-de-alcance + Changelog; **Fase 4 COMPLETA → proyecto DayZ-MCP cerrado** (todas las fases 0-4 PASS in-game).

### 2026-06-10 (noche, 2) — GATE 4A in-game PASS (conduce Claude)
- Sin juego: E3 install end-to-end (cazó **GATE4A-001**: Pillow ausente de requirements → pin 12.2.0); `mcp` desinstalado del user site → stdlib-only verificado de verdad (shim + 11 tests con python del sistema); registro real `claude mcp add` → `√ Connected`; smoke MCP 9/9 con cliente SDK (cazó **GATE4A-002**: `allow_reuse_address` de HTTPServer permite doble LISTEN en Windows → lock E4 roto → `ExclusiveThreadingHTTPServer` + `test_instance_lock.py`, suite 19/19; la suite de Codex no testeaba el lock).
- Con juego (`run-fase3.ps1 -NoStop` → swap del loopback por el server MCP con `fase3.key`): regresión FASE3_VERDICT PASS 6/6 → in-game 8/8 — bridge real → MCP tools end-to-end (pos real, captura PNG real ≈21k tokens, concurrencia, stale-key liveness, recovery 6.0s medido).
- 2 fixes del harness de gate (no producto): lock-test necesita 2º cliente MCP real (un server stdio pelado espera initialize); recovery-check por polling (3s fijos < backoff post-401).
- product-spec E3/E4 → ✓ in-game; E1 → 9/12; inbox actualizado. Pendiente: 4B.

### 2026-06-10 (noche) — Fase 4A implementada (Codex) + receptor PASS
- Codex implementó 4A desde el prompt scope-bounded (20 min): `dayz_mcp/` (loopback extraído stdlib-only con los 4 cambios §3.2; server FastMCP con 9 tools, mutex, 4-estados, lifespan start/stop), shim 5-endpoints, install `-Register`-gated, README, 11 unittests nuevos. Hallazgo C aplicado por Codex: FastMCP no acepta param `from` → alias público `"from"` + `from_pos` interno en scene_raycast (verificado server.py:260/269). Schemas citados contra MCPBridge.c/MCPClientBridge.c/mcp_client.py.
- **Receptor Claude**: 18/18 re-run independiente; scope por mtime limpio; constraints verificados en código (incl. mutex en `runtime.tool_lock` — grep inicial por `self.` dio falso negativo); `asyncio.to_thread` en captura (no bloquea el loop). ACEPTADO, 0 scope creep. Findings menores: mcp en user site (desinstalar tras crear `.venv-mcp`), `EXPECTED_BRIDGE_VERSION="4"` a respetar en 4B.
- Pendiente: **gate 4A in-game** (7 puntos del plan §8-4A + regresión). 4B sin empezar.

### 2026-06-10 (tarde) — Fase 4: grill + research + plan v1→R22→v2 + prompt impl 4A
- Grill Modo B en 2 lotes AskUserQuestion (7 adjudicaciones G-1..G-7): SDK `mcp`/FastMCP; 1 proceso 2 caras; E2 reformulado (hallazgo cláusula de desafío: el snapshot ERPCs protegía un riesgo que T-A eliminó); deuda fuera; `vehicle_drive`/`session_*` fuera + `bridge_status`; tramos 4A→4B.
- Research fase 0 SOLO de lo nuevo (excepción dual documentada): smoke install real `mcp==1.27.2` en Python 3.14.3 host + firmas inspeccionadas; SDK no serializa (`start_soon`) → mutex E4; `configure_logging`→stderr; hallazgo load-bearing: los `print()` del loopback a stdout corromperían el stdio MCP. Firmas Enforce 4B verificadas host-direct (`GetVersion game.c:944`, `SetDate world.c:51`/`GetDate :33`, `Weather weather.c:167/183-189`, `WeatherPhenomenon.Set :49`/`GetActual :38`, `GetWeather game.c:1349`).
- Plan v1 (12 tools E1, gates 4A/4B) → **R22 Codex**: approve with minor (R22-F4-001 FAIL handshake incompleto/fail-open + 4 WARN: lifecycle asyncio↔thread, negativos de gates, BUG-009..012 sin declarar, `/set_poll_delay` omitido de back-compat + 1 NIT). Receptor verificó host-direct los load-bearing → **v2** con los 6 aplicados (handshake `version_state` 4-estados + `EXPECTED_BRIDGE_VERSION` const + `--require-version`; `LoopbackServer` start/stop + `ServerState` encapsulado; fixtures negativos 4A.7/4B.7; BUGs declarados; `/set_poll_delay` preservado+test).
- Bajado al product-spec: filas E1/E2/E4 + Changelog 2026-06-10. Inbox: applied. Prompt impl 4A escrito (scope-bounded). NADA de código/PBO tocado.

### 2026-06-09 (noche) — Plan Fase 3 v2 + review adversarial (autónomo, usuario dormido)
- 4 adjudicaciones Grill Modo B vía AskUserQuestion (usuario despierto, pre-sueño): alcance = plan+review adversarial; CONFLICT-1 = presupuesto de tokens; CONFLICT-2 = síncrono+readiness; peer cliente = mínimo para Visual.
- 2 sub-agentes de recon host-direct (estado del bridge + patrón cliente RPC/cámara) → 2 supersesiones del research: **SUP-1** (indexado GAME_TEMPLATE-only → Camera entity), **SUP-2** (MissionGameplay sin OnRPC + bridge HTTP → poller cliente T-A).
- CONFLICT-1/2 aplicados al `product-spec.md` (D2 + Changelog). Plan v1 redactado → **review adversarial de 3 subagentes** (arq/traza · citas R2 · riesgos in-game) → **plan v2** integrando fixes. Review doc: `reviews/2026-06-09-adversarial-review-fase3-plan.md`.
- Review confirmó SUP-1/SUP-2 host-direct y T-A viable; cazó bugs (ventana de grab equivocada, freeze server-authoritative en peer equivocado, create_local, FOV getter, token budget ~320px, job machinery=port) + definió un **Spike 0 OFFLINE** (ping RestApi, selector ventana, calibración tokens) que de-risca sin rebuild.
- NADA de código/PBO/in-game tocado. Próximo: ratificar SUP-1/SUP-2 + 6 puntos → Spike 0 offline → (opcional) R22 Codex → implementación.

### 2026-06-09 — Fase 3 (Visual): research dual fase 0 cerrado + consolidado
- Research dual R24: Claude (5 sub-agentes Explore/general-purpose en paralelo, 1 por dimensión) + Codex CLI, independientes (anti-contaminación). Solape ~70% (clusters de hecho verificado) → dual justificado (<90% umbral de degradación).
- Convergencia (núcleo): cámara client-rendered (RPC a peer cliente nuevo), imagen host-side (no pasa por el engine), sin señal vanilla de frame-presentado (gate externo frame-diff). Familia cámara `enworld.c:53-65` + `world.c:19` + transporte `MCPBridge.c:190/1810` **spot-verificadas host-direct** por Claude (exactas).
- Deltas cruzados clave (cada mitad aportó load-bearing que la otra no tenía): **Codex** halló `CameraToolsMenu` (precedente vanilla get/set + `RPCSingleParam` + `IsInterpolationComplete`); **Claude** halló que el límite real es **~25k tokens** (Claude Code), no 1MB → **CONFLICT-1** corrige D2.
- Conflictos para el plan: CONFLICT-1 (D2 `<1MB`→tokens, adjudicar), C1 (¿cámara indexada mueve viewport? — 1er spike), C2 (contrato peer cliente nuevo).
- NADA de código/PBO/product-spec tocado. Docs: `research/2026-06-09-fase3-visual{,-claude,-codex}.md`. Handoff: `30_Sessions/2026-06-09-DayZ_MCP-fase3-research-fase0.md`.
- Próximo: plan Fase 3 (Grill Modo B), 1er gate = adjudicar CONFLICT-1.

### 2026-06-09 — Fase 2 cerrada: R21 procesada + X.5 (F2-001/F2-002) + re-run in-game PASS
- R21 fase-2 (Codex, sesión 27): needs-fix, 2 FAIL + 3 WARN. Claude verificó host-direct los 5 hallazgos + el contrato.
- Hallazgo de Claude que refinó la R21: el fix single-exact que Codex sugería para F2-001 rompería los negativos
  `fixture_not_found`/`parse_error` del harness (usan 3 paths bajo el prefijo, `mcp_client.py:21-23`/`963-970`) →
  ripple R7. Fix correcto = harden-prefix deny-by-default.
- X.5 correctiva (Codex, sesión 28): F2-001 (allowlist: prefijo + basename sin `/`,`\`,`..`) + F2-002 (fixture
  vacío → `parse_error`/`found=false`). Claude verificó host-direct ambas matrices + scope limpio (IsFiniteFloat/
  radius/harness/MCPMessages intactos — Codex resistió la tentación F2-004).
- Claude: re-build PBO (AddonBuilder `-packonly`) + deploy `P:\Mods\@DayZ_MCP` (verificado por contenido: PBO
  contiene `empty_fixture`) + re-run `run-fase2.ps1 -WaitInGameSeconds 300` → **GATE=PASS, overall_pass=true**
  (`_fase2/run_20260609_014239`). Ripple R7 cerrado in-game. 3 WARN → BUG-010/011/012 backlog.
- Próximo: research dual fase 0 de Fase 3 (Visual).

### 2026-06-07 — X.5 correctiva (sesión 9): harness arreglado, fase 0 certificada
- Codex aplicó los 4 fixes (2 P1 harness + 2 P2 bridge); Claude (receptor) verificó host-direct: A1 sin fallback
  (target=marker, distance 0.0313m, no 0.000), verdict-integrity (overall_pass exige A1-A5 + clientExit0), backoff
  tras parse-fail, Shutdown+destructor (`RestContext.reset()` restapi.c:133). Re-cert `run_160720` GATE=PASS legítimo.
- Adjudicado: el tell "~0.003m" era predicción errónea de Claude; el gate real (target=marker + <0.5m + ≠0.000)
  se cumple — 0.0313m es el asentamiento vertical real del player. fase 0 cerrada de verdad.
- Próximo: fase 1 (control).

### 2026-06-07 — A4+A5 closeout, R21 doble revisión, decisión X.5
- A4+A5 PASS (Codex sesión 7): fix contrato 403→401 (`mcp_server.py:77`, gap que cazó Claude pre-handoff);
  A4 401/401/401+400, A5 backoff+recovery en log real. POC "completo" provisional.
- R21 estructural (doble revisión independiente Claude + Codex sesión 8): transporte sano, pero 2 P1 del harness.
  Codex cazó el A1 tautológico que la mitad Claude subestimó; Claude (receptor) verificó host-direct que disparó
  en run_045213 y que la propiedad real de A1 igual se sostiene (0.0031 m). Consolidado + scope B (usuario).
- Próximo: X.5 sesión 9 (2 P1 + 2 P2 bridge + re-cert), luego fase 1.

### 2026-06-06/07 — POC fase 0: plan (R22) → Step 0 → A1+A2+A3 PASS
- Proyecto propio; product-spec (Modo A); plan POC (Modo B) + R22 Codex (approve w/ minor) → v2.
- Step 0 (smoke): FAIL deploy → PBO `-packonly`; FAIL compile `ERESTOPTION_*` → quitado; FAIL `config_url_empty`
  → config dual-path `$mission:`; **PASS** (transporte plumbing probado).
- Step 1 (POC real, A1+A2+A3): FAIL compile `ReadFromString` (3er param) + ternario `?:` (no existe en Enforce,
  error de Claude) → fix var string; FAIL `no_players` (mission bare no spawnea) → **fix mission chernarus completa
  (run-poc.ps1)**; **A1+A2+A3 PASS in-game 2026-06-07** (distance 0.0, ticks_in_flight 6420, ids 10/11).
- Lecciones: harness-apis §9 item 10 (proto en dump ≠ runtime); Enforce sin ternario; mission completa para spawn.
- Próximo: cierre A4+A5 + R21 del POC; luego fase 1 arquitectura.
