# fb-20260909-161615-c9ca — native_launcher_backend: native_job_cleanup_incomplete names the failed term as a fine_code that crosses the wire

Night lane 2026-09-10. Implemented by Grok (cursor-grok-4.6-xhigh via Cursor CLI) in an
isolated worktree branched from `30f257b96b`; reviewed by Codex (gpt-5.6-sol);
gates re-run by the receiver (Claude) with `gate-check.mjs --reverify`, never taken from the
worker's report. Full-suite baseline in the worktree: 15 red ids (docs tests that read
untracked files, the launcher registry/reconcile pair, and the ticket's own red where it
applies); the gate is "no NEW red by name", not zero reds.

## What changed

Se reescribio la docstring de NativeLauncherBackendError para el contrato real de wire (`code` y `fine_code` identifier-shaped via server._opaque_dayz_test_failure; `detail`/`event_kind`/`pid`/`image_path` locales), se fijaron los `detail` de los dos escenarios test_fb_c9ca_* del backend, y se anadio un caso de wire con el constructor real, token de 4 partes y silencio de `drain_s`/`open_handles`. Los dos modulos quedaron OK; la suite completa no anadio nombres rojos respecto a los 15 de BASELINE-REDS.txt.

Files: tools/dayz_mcp/native_launcher_backend.py, tools/dayz_mcp/server.py, tools/tests/test_native_launcher_backend.py, tools/tests/test_mcp_tools.py

## Gates (receiver's run)

- [x] G1a: tests.test_native_launcher_backend passes
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 42 tests in 11.157s | OK
- [x] G1b: tests.test_mcp_tools passes
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 53 tests in 8.437s | OK
- [x] G2: the worker's fb_c9ca tests exist and fail on the pre-fix product files, so they discriminate
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=restored: verified | red before fix confirmed
- [x] G3: the full suite adds no red by name over the 15-id worktree baseline
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=full suite: ran 3402 tests in 286.712s; reds now 15, baseline 15 | no new reds
- [x] G4: only the four allowed files changed and nothing was committed
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=touched: ['tools/dayz_mcp/native_launcher_backend.py', 'tools/dayz_mcp/server.py', 'tools/tests/test_mcp_tools.py', 'tools/tests/test_native_launcher_backend.py'] | write-set ok
- [ ] G5: the Codex Sol review of the diff leaves no P0 or P1 open
  pending

## Codex review

Verdict: APROBAR

- [P2] tools/dayz_mcp/native_launcher_backend.py:171 -- La docstring de `NativeLauncherBackendError` ahora contradice el contrato real al afirmar que todos sus campos estructurados son solo locales y no deben cruzar el MCP wire -- `server.py:370-372` reenvía deliberadamente `fine_code`; esta guía obsoleta puede inducir a eliminar o evitar el comportamiento requerido, pero no rompe la ejecución actual.
- [P2] tools/tests/test_native_launcher_backend.py:1609 -- Los tests nuevos del backend no fijan el contenido diagnóstico local exigido por el ticket -- Solo comprueban `code` y `fine_code` en `:1609-1612` y `:1661-1663`; reemplazar o vaciar las métricas de `native_launcher_backend.py:1517-1520` mantendría verdes esos tests, y el test de wire usa un `detail` fabricado en vez del error producido por el backend.

Round 2 (a fresh Grok session, same worktree) closed the findings above; the receiver re-ran every gate on the round-2 tree and read the delta.

Round-1 summary (the product change):
`native_job_cleanup_incomplete` ahora distingue los dos terminos de cleanup con `fine_code` identifier-shaped (`active_zero_never_observed` / `active_zero_wait_timed_out`); el detalle numerico queda en `detail` (solo log local) y el server anade `:<fine_code>` como cuarta parte del token de wire si pasa `_is_safe_error_token`. G1 verde. G3: la segunda pasada de la suite completa no anadio rojos por nombre respecto a los 15 de BASELINE-REDS.txt (la primera pasada tuvo un flake ajeno, ver DECISIONES). G2 y G5 no se ejecutaron aqui.
