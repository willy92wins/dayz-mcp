# Opus implementation review — disposition

Native CLI served claude-opus-5, 53 turns, 526.48 s, complete, no permission denials.
Verdict: SOUND-with-fixes. Full review in `review.md`; no claim of in-game validation.

- P2-1 accepted. Merely timing out acquisition would not fix an OS call hung while
  holding the lock. `_steam_mutation_allowed` now captures run state, releases the
  lifecycle lock before retail/diag/identity/argv I/O, then reacquires for a state
  comparison. A bounded probe slot prevents accumulating hung read-only threads.
  Regression blocks argv_of: the operation lock remains obtainable, start returns
  its preparation timeout, and no permit is emitted later.
- P2-2 accepted. Named preparation refusals and uncertain offline/client transport
  never replay start. Server creation retains its established idempotent retry;
  uncertain new offline launches use exact-run cleanup. Both returned transport
  failure and thrown transport exception are exercised. No fresh Steam budget.
- P3-1 accepted. Reader termination has a separate broken/EOF event, so a full queue
  cannot lose its only EOF signal and force the entire preparation timeout.
- P3-2 optional suggestion declined: the production default must protect every
  lifecycle client consumer. Existing tests explicitly inject `FakeSteamGate`;
  the only real helper tests spawn harmless Python programs. No test repairs Steam.
- P3-3 retained explicit limitation: a restarted Steam whose current marker has
  already rotated beyond 128 KiB cannot be certified and times out conservatively.
  Real DayZ/Steam startup and rapid relaunch remain deployment acceptance.

Additional Codex fixes since the initial review snapshot:
- `reservation_active(expire=False)` gives the monitor exact TTL/authority evidence
  without running a slow release hook before cancelling the helper. Default callers
  retain the previous expiry/cleanup behavior. Expiry-before-cleanup test passes.
- Closed provider methods replace dynamic getattr forwarding; the existing security
  runtime audit now passes without exclusions or a weakened scanner.
- Helper cleanup exceptions retain the fence; late terminal messages cannot bypass
  the absolute deadline; worker preserves helper cleanup degradation.

Validation: `opus-fix-tests.txt`: 132 tests PASS; `authority-tests.txt`: 319 PASS;
`suite-fixes.txt`: 268 PASS, 5 documented platform/environment skips. The broad first
suite had 3345 tests and found the old mocks and a port-probe timing fixture; both
are fixed. Editable installation was missing when its first subprocesses started;
after installing the isolated project, startup identity tests pass.

Remaining broad-suite environmental failures: task9 protocol tests require files
not present in the Git base (`CLAUDE.md`, `AGENTS.md`, `test-contracts/task9-protocol-docs`);
one H8 test reads an unversioned `tools/.dayz_mcp.key` despite mocking transport. No
live key is copied and no fixtures are fabricated to call the complete suite green.

## Final verification closure

Opus confirmed P2-1, P2-2 and P3-1 closed in `verification.md`. Its residual
diagnostic suggestions are handled: lock contention reports `steam_prepare_busy`,
and a retained read-only probe slot reports `steam_probe_pending`. A test proves
that this blocks further mutations while healthy readiness still works. The
post-commit rejection test fixes the run_id/cleanup discriminant. Remaining
read-only probe lifetime and conservative uncertain-run cleanup are explicit
limits, not claims of an orphan game process.

A final narrow Opus pass (`residual.md`) returned SOUND in 21.21 s. Total review
time remained within the original 17-minute pool. Final tests: 567 implementation
and 81 launcher tests PASS. Broad suite: 3351 tests, same 12 environmental failure
events as the clean base. The base's two extra missing-bundle checks passed after
building it. No candidate-only failure ID remains. `VALIDATION.md` records exact
commands, artifact hashes and the deployment boundary. This closes the evidence
request in the second review without claiming the full suite or Steamworks green.
