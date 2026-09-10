# fb-20260907-163855-c261 — control_client: the untrusted-session hint names server_reload as the in-session recovery

Night lane 2026-09-10. Implemented by Grok (cursor-grok-4.6-xhigh via Cursor CLI) in an
isolated worktree branched from `30f257b96b`; reviewed by Codex (gpt-5.6-sol);
gates re-run by the receiver (Claude) with `gate-check.mjs --reverify`, never taken from the
worker's report. Full-suite baseline in the worktree: 15 red ids (docs tests that read
untracked files, the launcher registry/reconcile pair, and the ticket's own red where it
applies); the gate is "no NEW red by name", not zero reds.

## What changed

The untrusted-policy hint on client_policy_untrusted_open_new_session now names server_reload (replace the serving process, re-read registration, re-accredit) before the operator fallback and still states that this client cannot open a new host session by itself, with the policy_cause= prefix unchanged; tests.test_control_client (36) and tests.test_shareconflict_windows (6) are green, and the full suite's red names match the 15-id baseline (a first full pass had one extra flake, PermissionError in test_runtime_root_lock_has_cross_process_ownership_and_recovers, which was absent on the second pass).

Files: tools/dayz_mcp/control_client.py, tools/tests/test_control_client.py

## Gates (receiver's run)

- [x] G1a: tests.test_control_client passes
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 36 tests in 0.584s | OK
- [x] G1b: tests.test_shareconflict_windows passes (it pins the policy_cause prefix)
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=Ran 6 tests in 4.527s | OK
- [x] G2: the worker's fb_c261 tests exist and fail on the pre-fix control_client.py, so they discriminate
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=restored: verified | red before fix confirmed
- [x] G3: the full suite adds no red by name over the 15-id worktree baseline
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=full suite: ran 3401 tests in 290.403s; reds now 15, baseline 15 | no new reds
- [x] G4: only the two allowed files changed and nothing was committed
  exit=0; shell=C:\windows\system32\cmd.exe; path=8692a0b9ff4d/57 entries; output=touched: ['tools/dayz_mcp/control_client.py', 'tools/tests/test_control_client.py'] | write-set ok
- [ ] G5: the Codex Sol review of the diff leaves no P0 or P1 open
  pending

## Codex review

Verdict: APROBAR

Ninguno.
