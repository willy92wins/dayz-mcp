# Steam launch admission â€” implementation contract

Approved scope: LFTurret `reviews/steam-diagnostic-2026-09-10/PR-PROPOSAL.md`, v3,
SHA256 `664C89696447451345773E91869535F424BE52F710195F45D15417F3FAEAE4F3`.
Base: `1c4c0eed5cadf13b994d09f3217dd04bb9002bc8`, isolated branch
`fix/steam-launch-admission`. No live daemon, launcher registry or game changes.

## Plan and authority

1. Carry strict, default-false Steam remediation consent from public request to
   sealed worker and daemon. Public validation reads only; server/preflight skip.
2. Pin Steam PID and creation time. Recent/restarted processes require their
   lifetime's startup marker; old stable processes permit an explicit limited
   unobserved-marker result, including after PID-only repair.
3. Prepare Steam after exact lifecycle admission and before committing the
   reservation, changing the manifest, replacing clients or minting instances.
   Release the operation lock during preparation; retain Steam exclusivity and
   monitor exact reservation authority. Revalidate admission after reacquiring.
4. Isolate mutators in a supervised helper. Cooperative cancellation plus an
   absolute 215 s deadline and <=5 s cleanup after detection. Verify helper exit;
   unresolved cleanup retains the Steam fence. Final spawn check is read-only.
5. Run regression tests, build an isolated sealed candidate, obtain Opus review,
   fix reproduced findings and submit a draft PR against the source branch.

No Enforce/client-server script change: public stdio -> sealed request/worker ->
authoritative daemon -> supervised Steam helper. Steam identity and MCP run,
lease and instance identities remain separate. No persistence format change.

## Acceptance / closed counterexamples

- [x] Consent rejects non-booleans; legacy requests remain accepted; sealed parity.
- [x] Invalid/foreign run, quarantine or concurrent preparation produces no writer.
- [x] Recent coherent PID waits; old stable missing marker passes with limitation.
- [x] Old stale registry repaired over the same process does not wait 180 seconds.
- [x] Previous lifetime marker, PID reuse, changed identity cannot authorize spawn.
- [x] Queue/server-wait changes are detected by daemon; final check catches drift.
- [x] Preparation leaves run state, current client and instance bindings untouched.
- [x] Admission/ownership/ports/quarantine rechecked after wait outside operation lock.
- [x] Own live client + required mutation refuses without killing/replacing it.
- [x] Authority loss/deadline cancels cooperative and hung helpers within budget;
      unverified helper exit remains fenced, no further writer admitted.
- [x] No opt-in means no writes or invokes; server/preflight never prepare Steam.
- [x] Relevant existing lifecycle/worker/request/launcher tests pass.
- [x] Candidate sealed build passes without touching the installed launcher.
- [x] Opus reviews actual implementation; actionable reproduced findings fixed.

Test interpreter: worktree `tools/.venv-mcp/Scripts/python.exe`; cwd `tools`.
Tests use fake OS providers/processes and real coordinator authority transitions,
plus a harmless supervised subprocess for cancellation. Never mutate live Steam
as a unit/integration test. In-game rapid close/relaunch with VPP remains a separate
controlled validation after coordinated deployment; simulated PASS cannot prove it.

Review budget: 17 minutes total for the main review and fix verification. Used
526.48 s + 338.81 s; a final 21.21 s check of the residual diagnostic changes used
the remaining budget. Final narrow verdict SOUND. Stop conditions satisfied for
implementation; deployment acceptance remains explicitly pending below.

## Completion evidence

648 final directed tests PASS (567 + 81); full suite 3351 tests, 33 skips,
11 failures + 1 error from absent unversioned task9 docs/H8 local key. All 12
failure events reproduce on the clean base; no candidate-only failure. See
VALIDATION.md. Sealed candidate rebuilt reproducibly after the final code changes.
No live deployment, game launch, registry mutation or Steamworks certification.
A permanently hung read-only probe retains one slot: further mutation requests
report steam_probe_pending, while healthy read-only preparations remain usable.
Wait for the probe or coordinate a daemon restart to restore mutations.
