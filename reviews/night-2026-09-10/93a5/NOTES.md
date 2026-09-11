# fb-20260906-201129-93a5 — tests: the lifecycle request fixture composes what the worker composes, with a parity module that fails on divergence

Night lane 2026-09-10. Implemented by Grok (cursor-grok-4.6-xhigh via Cursor CLI) in an
isolated worktree branched from `30f257b96b`; reviewed by Codex (gpt-5.6-sol);
gates re-run by the receiver (Claude) with `gate-check.mjs --reverify`, never taken from the
worker's report. Full-suite baseline in the worktree: 15 red ids (docs tests that read
untracked files, the launcher registry/reconcile pair, and the ticket's own red where it
applies); the gate is "no NEW red by name", not zero reds.

## What changed

Both review findings closed. The parity helper now compares full key sets after an explicit worker-only allowlist; `test_fb_93a5_rejects_an_unmodeled_fixture_key` failed against the round-1 helper (`AssertionError not raised`) and is green after the change. `client_without_run_id` uses `run_id=None` on both sides. The `request()` comment states the worker/tool rule actually implemented. `tests.test_lifecycle_request_fixture_parity` is green (5/5). `tests.test_lifecycle_reconcile` still has exactly its two known reds. The full suite added no red test names over BASELINE-REDS.txt.

Files: tools/tests/test_lifecycle_reconcile.py, tools/tests/test_lifecycle_request_fixture_parity.py

## Gates (receiver's run)

- [x] G1a: tests.test_lifecycle_reconcile has no red beyond its two baseline reds
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=tests.test_lifecycle_reconcile: ran 94 tests in 1.269s; reds now 2, baseline 2 | no new reds
- [x] G1b: the new parity module passes
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 5 tests in 0.011s | OK
- [x] G2: the parity tests fail against the OLD fixture file, so they discriminate
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=restored: verified | red before fix confirmed
- [x] G3: the full suite adds no red by name over the 15-id worktree baseline
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=full suite: ran 3404 tests in 284.021s; reds now 15, baseline 15 | no new reds
- [x] G4: only the two allowed test files changed or appeared and nothing was committed
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=touched: ['tools/tests/test_lifecycle_reconcile.py', 'tools/tests/test_lifecycle_request_fixture_parity.py'] | write-set ok
- [ ] G5: the Codex Sol review of the diff leaves no P0 or P1 open
  pending

## Codex review

Verdict: CORREGIR

- [P1] tools/tests/test_lifecycle_request_fixture_parity.py:119 -- La suite no compara los conjuntos de claves completos y el caso `client_without_run_id` no usa el mismo `run_id` en ambos lados, por lo que puede aprobar con fixture y worker divergentes -- `_assert_witness_key_sets` descarta toda clave fuera de `_MODELED`; además, en :136 `self.fixture.request()` conserva `run_id` (test_lifecycle_reconcile.py:222), mientras :137 construye el core con `run_id=None`, y :138-139 solo comprueba la ausencia del witness. El caso obligatorio ya tiene conjuntos distintos y queda verde, reproduciendo la clase de falso positivo que el ticket pretendía cerrar.
- [P2] tools/tests/test_lifecycle_reconcile.py:224 -- El comentario nuevo atribuye a `_start_core` la regla «solo al sustituir un cliente vivo», pero esa función no conoce la vitalidad ni si habrá sustitución -- dayz_test_worker.py:325-332 copia el witness únicamente según `run_id`, rol y tipo; dayz_test_tool.py:980-990 también autoriza las ramas sin cliente y con PID muerto, y :1567-1577 envía el witness para cualquiera de ellas. El propio test de registro muerto usa `with_witness=True` en test_lifecycle_reconcile.py:845, contradiciendo el comentario.

Round 2 (a fresh Grok session, same worktree) closed the findings above; the receiver re-ran every gate on the round-2 tree and read the delta.

Round-1 summary (the product change):
`LifecycleReconcileTest.request()` now matches production: the witness is absent by default and only stamped with `with_witness=True`. The 22 `self.request(` call sites were audited; the df53 test dropped its `pop()`; `_replacement` and every start_run that actually supersedes a client record pass the flag. New `tests.test_lifecycle_request_fixture_parity` drives real `_start_core` and is green (4/4). `tests.test_lifecycle_reconcile` still has exactly the two known reds. The full suite added no red test names over BASELINE-REDS.txt.
