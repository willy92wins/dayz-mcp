# fb-20260908-002541-296f — steam_preflight: the Steam invoke flags say what Windows does, DETACHED_PROCESS alone

Night lane 2026-09-10. Implemented by Grok (cursor-grok-4.6-xhigh via Cursor CLI) in an
isolated worktree branched from `30f257b96b`; reviewed by Codex (gpt-5.6-sol);
gates re-run by the receiver (Claude) with `gate-check.mjs --reverify`, never taken from the
worker's report. Full-suite baseline in the worktree: 15 red ids (docs tests that read
untracked files, the launcher registry/reconcile pair, and the ticket's own red where it
applies); the gate is "no NEW red by name", not zero reds.

## What changed

`_STEAM_INVOKE_FLAGS` en `steam_preflight.py` queda como `DETACHED_PROCESS` solo (el OR con `CREATE_NO_WINDOW` era dead code: Windows lo ignora junto a `DETACHED_PROCESS` y `steam.exe` es GUI), con comentario de cabecera y dos tests `test_fb_296f_*` que fijan el valor y que `invoke_steam` pasa `creationflags=_STEAM_INVOKE_FLAGS` y `close_fds=True` a un Popen falso. G1 verde (19 tests, exit 0). G3 verde: la suite completa no anadio ningun rojo por nombre respecto a los 15 de BASELINE-REDS.txt. G4: solo los dos ficheros permitidos modificados; HEAD no se movio.

Files: tools/dayz_mcp/steam_preflight.py, tools/tests/test_steam_preflight.py

## Gates (receiver's run)

- [x] G1: tests.test_steam_preflight passes
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 19 tests in 0.001s | OK
- [x] G2: the worker's fb_296f tests exist and fail on the pre-fix steam_preflight.py, so they discriminate
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=restored: verified | red before fix confirmed
- [x] G3: the full suite adds no red by name over the 15-id worktree baseline
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=full suite: ran 3401 tests in 292.397s; reds now 15, baseline 15 | no new reds
- [x] G4: only the two allowed files changed and nothing was committed
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=touched: ['tools/dayz_mcp/steam_preflight.py', 'tools/tests/test_steam_preflight.py'] | write-set ok
- [ ] G5: the Codex Sol review of the diff leaves no P0 or P1 open
  pending

## Codex review

Verdict: APROBAR

Ninguno.
