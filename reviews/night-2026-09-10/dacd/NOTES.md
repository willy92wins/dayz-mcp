# fb-20260909-165046-dacd — dayz_tools_paths: an injectable registry reader makes the layout tests host-independent

Night lane 2026-09-10. Implemented by Grok (cursor-grok-4.6-xhigh via Cursor CLI) in an
isolated worktree branched from `30f257b96b`; reviewed by Codex (gpt-5.6-sol);
gates re-run by the receiver (Claude) with `gate-check.mjs --reverify`, never taken from the
worker's report. Full-suite baseline in the worktree: 15 red ids (docs tests that read
untracked files, the launcher registry/reconcile pair, and the ticket's own red where it
applies); the gate is "no NEW red by name", not zero reds.

## What changed

`test_read_registry_string_swallows_every_bad_input` now models a missing key, a missing value, a non-REG_SZ value, and an invalid hive through `unittest.mock.patch` on `winreg.OpenKey` and `winreg.QueryValueEx`, so the host registry is never opened and every bad input still yields None. Reviewer calibration exits 1. Module suite is green (16 tests, exit 0). Full suite adds no new red names over BASELINE-REDS.txt (the round-1 `test_require_names_every_tried_tools_root_when_none_exist` stays green; the other 14 baseline ids remain).

Files: tools/dayz_mcp/dayz_tools_paths.py, tools/tests/test_dayz_tools_paths.py

## Gates (receiver's run)

- [x] G1: tests.test_dayz_tools_paths passes on this host with Steam registered at the default root
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 16 tests in 0.006s | OK
- [x] G2: the worker's fb_dacd tests exist and fail on the pre-fix source, so they discriminate
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=restored: verified | red before fix confirmed
- [x] G3: the full suite adds no red by name over the 15-id worktree baseline
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=- tests.test_dayz_tools_paths.DayZToolsPathsTest.test_require_names_every_tried_tools_root_when_none_exist | no new reds
- [x] G4: only the two allowed files changed and nothing was committed
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=touched: ['tools/dayz_mcp/dayz_tools_paths.py', 'tools/tests/test_dayz_tools_paths.py'] | write-set ok
- [ ] G5: the Codex Sol review of the diff leaves no P0 or P1 open
  pending

## Codex review

Verdict: CORREGIR

- [P1] tools/tests/test_dayz_tools_paths.py:304 -- `test_read_registry_string_swallows_every_bad_input` sigue usando el lector real del registro del host -- Las llamadas de las líneas 307-308 pasan hives válidos y alcanzan `winreg.OpenKey` mediante `tools/dayz_mcp/dayz_tools_paths.py:113-123`; parchear `winreg.OpenKey` para lanzar `AssertionError` hace fallar exactamente esta prueba, y un valor real `NoSuchValue` de tipo string también rompería su expectativa de `None`.
