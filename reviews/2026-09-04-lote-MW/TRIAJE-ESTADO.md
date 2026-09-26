# Triaje del buzón — sesión local_32a70c44 (2026-09-04)

Fuente: `inbox_snapshot.json` (59 sin resolver, tomado ~04:05). Peer: `local_a2542858-8bf1-43a8-b286-e7308c3abde3`
(retiene 16 `4d66`, 17 `7c88`; write-set: process_lifecycle.py, loopback.py, daemon.py, dayz_test_tool.py región
execute_dayz_test_stop/resolve_stop_run, server.py región dayz_test_stop ~:3039-3063; no comitear dayz_test_tool.py hasta su aviso).
Rama compartida `work/inbox-20260830-modules` en `DayZ_MCP_dev` (git funcional ahí). Stage por rutas exactas.
Suite: `cd tools && ./.venv-mcp/Scripts/python.exe -m unittest discover -s tests -t .` → baseline 2309, failures=2 (centinela MCPBridge.c).
Criterio de cierre vigente (HANDOFF): commits atribuibles · suite verde MEDIDA nombrando intérprete · app.pyz arranca ·
revisión otra familia (tope 2 rondas) · pipeline_resolve citando commit+suite+pyz+dictamen.

## A. FUERA (no tocar): gate in-game (humano con juego) — 14
01 3bb4 · 05 8f8c (partes in-game) · 15 47c9 · 18 141e · 20 4407 · 25 20be · 28 b2c4 · 29 f4f2 · 30 0de3 · 32 668f · 33 2762 · 34 40e4
· 4f83 (restore_gameplay ok:1 — Enforce + in-game) · bd90 (centinela MCPBridge.c: se recongela DESPUÉS del canario in-game)

## B. DEL PEER: 16 4d66 · 17 7c88 · 76dd (daemon idle-restart mata runs: daemon.py, lote H) → avisarle, no resolver

## C. DECISIÓN DEL HUMANO (parcar con propuesta) — 
19 344d (dirección de versión LFPowerGrid 1.2.3→1.2.4) · 0fde (texto de promotion-gate.ps1) · 077d (receta aislamiento claude -p,
CLAUDE.md G7/skill) · 26 d366 + 07 9b7b (exigen M23 promotor XL: promote_effective_schema.py + autoridad + calibración/CAS/journal;
alternativa barata: comparar generación daemon vs arranque de sesión, prohibida por la hoja) · 2bd3 (transición replace del registry:
persistencia encadenada → R9) · df93 (preflight VPP en ruta secure_launcher: worker/dayz_test_tool + in-game) · 
5ca4 (lección gates-ledger → skill edit)

## D. LOTE M (Grok run 1) — server.py superficie pública + tests  [server.py fuera de :3039-3063]
M1 f6fa + 08 c7ca: pipeline_resolve(evidence_ref opcional ≤240) → inbox.append_resolution; required intacto; round-trip público.
   server.py:4516-4527 · inbox.py:121-146 · test_pipeline_feedback.py:246-281.
M2 23 782b (mitad M22): dayz_knowledge_status/prepare → schema {properties:{}, required:[], additionalProperties:false} + rechazo
   de extras `bad_args: unexpected arguments` (isError True por sesión cliente real, in-memory o stdio). Patrón: _patch_public_argument_alias
   server.py:1718-1731. Registro: knowledge.py:656,:665. Generalizar helper y aplicarlo también a las tools que ganan params en este lote (ea10).
M3 03 268a (mitad M22): capture_screenshot gana crop_space (default "client") reenviado a mcp_capture.capture_dual (mcp_capture.py:644);
   devolver [Image, JSON meta] también con save_fullres=False (server.py:4027-4029); test público por app.call_tool con save_fullres F/T.
M4 52cf + b12d: ui_click/ui_reload_layout `mode: Literal["direct","complete"]` (server.py:4241, :4281); describir mode_not_implemented.
M5 14ab: ui_click `button: StrictInt` (medido: 0 usos de button como cadena/bool/float en %LOCALAPPDATA%\DayZ_MCP).
M6 27 ffc7 / 31 9d46 (parte wire): dayz_test_run `mode` publica enum derivado de dayz_test_modes (server.py:2901); test enum == autoridad M12.
M7 1ad0: descripción dayz_test_run: extra_mods acepta cualquier carpeta bajo mod_roots del proyecto.

## E. LOTE W (Grok run 2) — ergonomía/tests/tools sueltos
W1 05.4 action_use: `action` = classname Enforce (Type().ToString()), no el texto visible (server.py:4365 desc).
W2 05.5 entities_query: reason="no_player_connected" sólo con ok + lista acreditada vacía (server.py:1832-1857).
W3 05.2 wait_for: timeout_s>600 → `bad_args: timeout_s must be <= 600` antes de sonda/sleep; doc del techo (server.py:2269, :4408).
W4 05.1 wait_for: reintento sólo con igualdad exacta `game_not_ready:reason=server_poll_stale`, sleep fuera del lock, deadline único.
W5 ef5e bridge_status desc: espacio de reason abierto (+_FENCE_BLOCK_READY) (server.py:4044).
W6 e338 h9_native_probe.py: pasar daemon_policy_json (serialize_normal_daemon_policy(load_normal_daemon_policy()) como secure_launcher.py:144-151);
   main() no colapsa TypeError a código de bundle.
W7 cb40: portar test_effective_schema.py (14 funciones pytest) a unittest.TestCase.
W8 363c(1): doctor verifica sha+size de entradas external del closure-manifest y nombra el remedio.
W9 da54: guard de intérprete en la suite (test que falla nombrando el intérprete aprobado cuando sys.executable no lo es).
W10 eca9: build_native_launcher.py imprime el next-step (rollback-last → install --expected-sha256 <sha del REGISTRY>) al terminar.
W11 70c9: native_bundle.py:483-484 — si packaged (sí, en _APP_PACKAGED_MODULES? NO: native_bundle.py no está en la lista) → error con hint
   `bundle_layout_newer_than_bridge: restart the MCP bridge` cuando el conjunto de externals difiere. [verificar que no cambia el bundle]

## F. YO (pequeño, fuera del repo MCP): bb66 vault-link-audit.ps1 Get-SourceClass promoted-snapshots ≠ live; correr el doctor.

## G. ADMINISTRATIVOS (pipeline_resolve con evidencia, sin código)
da54 (incorporada; guard W9) · b8d8 (superada por da54) · b12d (DAG retirado; doc en M4) · e914 (autoridad ABANDON; LL-432) ·
3db9 (registrado; product-spec congelado; cláusula de universo propuesta) · f715 (arreglado 09-02; sin generador, a mano) ·
97bc, 9145 (mediciones registradas; cadena de cierre retirada por el plan de triaje) · 8fac, 21bc, 5ca4 (lección LL nueva) ·
eca9 (memoria + W10 + documentar en runbook) · 7a9f (alias lfheli en policy:372; runner foreign: vía soportada = dayz_test_run con alias) ·
1ad0 (registrado + M7) · 63d4 (LL-392) · 62d7, eb6b (guard del ARNÉS Claude Code: no editable; workaround Bash/`git commit -F`) ·
b224 (pista ya en dayz_test_tool.py:23; valores de mode en el error desde lote E) · 7f67 ([1][2] ya arreglados —verificado por ejecución—,
[3] limitación declarada, [4]-[7] por diseño) · 10 251d (EVIDENCIA: tests test_wait_for.py; añadir arm 0 sobre mismos bytes → Lote W?) ·
ea10 (decisión aplicada en M2: additionalProperties:false en tools que ganan params).

## Presupuesto: Codex 77 % semana (reset 09-07), ~1000 s/revisión → UNA revisión Codex sobre M+W juntos (2 rondas máx).
Grok: briefs pequeños (<50k tokens), $≈1/lote.

## PROGRESO (actualizar)
- 04:35 Grok CLI 402 (saldo Grok Build agotado) tras 15 turnos/$0.17, 0 ficheros. Evidencia: lote-M/runs1/grok-402/.
  Lane sustituida por Cursor Composer (composer-2.5) con harness/runner_cursor.sh (brief como ws/BRIEF.txt, cwd=ws,
  --trust --force --workspace --output-format stream-json). cursor-agent 2026.09.02-c22c1a3.
- 04:41 lanzados lote-M y lote-W en Cursor en paralelo, con watch.sh (stall 2400 s). Gates sellados (runs1/GATE-SEAL.txt).
- RESUELTAS (11): e914 3db9 97bc 9145 63d4 62d7 eb6b 7f67 b224 7a9f bb66 (bb66: vault-link-audit.ps1 editado, 124→1 pares).
- PENDIENTES ADMIN tras lotes: da54 b8d8 (W9) · b12d 1ad0 ea10 (M) · f715 (W12) · eca9 (docs README-mcp + runbook) ·
  70c9 363c (W8 parcial; (2)(3) piden native_bundle/dayz_test_tool → declarar) · cb40 (W7) · 8fac 21bc 5ca4 (LL en post-session).
- HUMANO: 19 344d · 0fde · 077d · 26 d366 + 07 9b7b (M23 XL) · 2bd3 (R9) · df93 · 4f83 · bd90 + 12 in-game · 76dd (peer).
- Lote W: build_native_launcher.py FUERA (su sha256 va en _build_contract:717-729 → editarlo invalida el bundle); W10 → docs.
- 04:50 Cursor entregó M y W A CIEGAS (mis hooks PowerShell bloquean su shell). Recepción: sellos 4/4, write-sets limpios.
  M: G1 48/50 → parches orquestador (required [] en helper; test webp fuera del write-set) → G1 50/50 VERDE, G2 OK, 12 tests nuevos.
  W: G1 16/16; G2 test_doctor 9 rojos (finding INFO en corridas sintéticas) → parche orquestador (DoctorSources.native_launcher_id,
  gating, default_sources="dayz-test-v1", 2 tests wiring). Notas: lote-*/runs1/RONDA2-ORQUESTADOR.md.
- eca9 RESUELTA (docs README-mcp.md paso 9 + runbook H9). bb66 RESUELTA. Total resueltas: 12.
- 05:00 gates W VERDES (16/16, suite acotada OK, doctor real: NATIVE_BUNDLE_EXTERNALS_OK checked=23). Diffs: review-MW/DIFF-M.patch
  (+218 -17), DIFF-W.patch (+586 -199). Codex review lanzado desacoplado (Start-Process pid 42088, gpt-5.6-sol, review-MW/, Monitor
  bee51wuv3 vigila EXIT/stall). Integración: integrar.py copió M (5 ficheros) y W (7) al árbol vivo; suite completa en background
  (task bdc32gopu, log lote-W/integ-suite.log). Test extra del orquestador: test_p3_public_round_trip_... (13 tests M OK, copiado).
  G-PYZ: pyz_closure_check 'pyz import closure verified' (16 en pyz / 15 declarados); imports con el runtime del bundle OK; ninguno
  de los módulos tocados está empaquetado.
- Commit: NO hecho todavía (esperar veredicto Codex; git commit --only rutas exactas; mensajes en lote-*/COMMIT-MSG.txt).
- Resoluciones redactadas en RESOLUCIONES-MW.md (16 fichas) con placeholders {CM} {CW} {SUITE} {PYZ} {CODEX}.
- 31 9d46 queda ABIERTA (copias en dayz_test_tool.py = fichero del peer; worker empaquetado = rebuild). 26/07 abiertas (M23 XL).
- 05:12 suite completa tras integrar: Ran 2355, failures=3 errors=3 → 4 nuevos en test_box_occupancy (DayzTestRunWaitForBoxTest usa
  mode="offline" con fachada simulada; enum P5 lo rechaza). Arreglado en el árbol vivo: 6 literales offline→server (líneas 676,700,
  726,799,829,869) → módulo Ran 31 OK. Peer avisado (fichero suyo). Falta re-medir la suite completa tras el commit.
- Añadir a la tanda de resoluciones: 70c9 (docs: reabrir sesión cliente; hint en native_bundle.py declinado: verificador compartido).
- 05:25 SUITE COMPLETA post-fix (tools/.venv-mcp/Scripts/python.exe): `Ran 2356 tests in 226.591s FAILED (failures=2)` — los dos
  rojos por nombre: test_task9_spawn_phase_markers.test_full_source_hash_is_frozen y test_removing_only_marker_lines_restores_frozen_source_hash
  (centinela MCPBridge.c, ficha bd90). Baseline 2309 → +47 tests. Log: scratchpad/suite-post-integracion.log.
- {SUITE} = "Ran 2356 tests, FAILED (failures=2) con tools/.venv-mcp/Scripts/python.exe; los 2 rojos son el centinela de MCPBridge.c (bd90)".
- SP-378 (delegar/cursor-cli) en el ledger; memoria cursor-headless-shell-blocked-by-hooks; parche de modos para el peer en
  scratchpad/parche_modos_dayz_test_tool.py (enviado).
- 05:40 CODEX ronda 1: BLOQUEANTES=4 (review-MW/REVIEW-CODEX.md, 20,7 KB): B-01 P5 enum congelado al import; B-02 W7 assertTrue vs `is True`;
  B-03 W8 manifest ilegible → WARN → doctor ok; B-04 W8 remediación con sha del PE. Los 4 ARREGLADOS en DEV y ws: helper
  _patch_mode_enum_from_authority (build-time + por llamada) + test de sustitución; assertIs(True,...); UNREADABLE → FAIL (+test);
  sin approved_sha256 (placeholder "sha del registro tras rollback-last"). Backlog aplicado: W9 resolve() antes de comparar;
  descripción de capture_screenshot anuncia los dos bloques + frame_sha256 (8f76(a)). Backlog NO aplicado (documentar): composición
  alias+closed (conjuntos disjuntos hoy), test W6 laxo (rc=="raised" aceptado), test_docs_truth rojo ambiental en el ws.
- 05:50 COMMITS en work/inbox-20260830-modules: {CM} = e603c16 (M, 6 ficheros +313 -25) · {CW} = 00d4303 (W, 7 ficheros +582 -191).
  decisions/decision-log.md sigue staged por otra sesión, intacto. Diffs v2 (vs d007838): review-MW/DIFF-M-v2.patch, DIFF-W-v2.patch.
  Codex ronda 2 lanzada (BRIEF-R2.txt → REVIEW-CODEX-R2.md, EXIT-R2). Suite completa post-commit corriendo (suite-post-commit.log).
- 06:00 SUITE FINAL en HEAD 00d4303: `Ran 2357 tests in 225.776s FAILED (failures=2)` — solo los 2 del centinela (log suite-post-commit.log).
- SIGUIENTE: veredicto R2 → (fix commit si hace falta) → resoluciones (RESOLUCIONES-MW.md con SHAs) → HANDOFF LIVE-STATE → 30_Sessions
  → LL-445..448 → informe final.
- 05:15 buzón vivo: 51 sin resolver (12 cerradas por mí + 4 NUEVAS de otras sesiones): 1f85 (skill dayz-test-ingame: workspace dentro
  de la skill → humano/skill), cabd (LFHeli run_batch_f1.ps1 vs 2302 → humano/LFHeli), 8f76 (capture frame stale: (a) cubierto por
  M frame_sha256; (b)(c) in-game + peer), d60f (UNRECONCILED tras cuelgue → peer lote H). Peer avisado de d60f y 8f76(c).
