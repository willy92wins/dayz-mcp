# gpt-6.1-sol review, g5_docs, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Environment-only selection is incorrectly offered.**  
[tools/README-mcp.md:60](/C:/Users/guill/dzmcp_gauntlet/g5_docs/ws/tools/README-mcp.md:60) says the selector can be supplied through flags “or” environment variables.

- **Scenario:** Set `DAYZ_MCP_INSTANCE=alpha`, leave the other selector variables unset, then run `python install_mcp.py --register`.
- **Actual:** Argument parsing exits **2**, reporting `instance_environment_conflict`; it does not select `alpha`. Reproduced through `install_mcp.parse_args`, without installation.
- **Evidence:** `tools/dayz_mcp/server_cli.py:444` requires the environment to match the flag-derived token; `tools/install_mcp.py:859` invokes that check.
- **Fix:** Say that flags select the instance. Environment variables, when present, must match them and cannot replace them.

**F2 — P2 — H16 promises an unsupported absence of installer effects.**  
[product-spec.md:151](/C:/Users/guill/dzmcp_gauntlet/g5_docs/ws/product-spec.md:151) includes a relative shared root among inputs rejected without writing a sidecar, venv or registration.

- **Scenario:** On a fresh tools tree, set `DAYZ_MCP_SHARED_ROOT=relative` and run `python install_mcp.py --register --instance alpha --game-path "C:\Games\DayZ" --port 8766`, with otherwise valid prerequisites.
- **Actual:** The installer accepts the selector and reaches venv creation rather than rejecting `relative_shared_root` before effects. A read-only probe intercepted the actual `("-m", "venv", …)` call.
- **Evidence:** `tools/install_mcp.py:1702` enters runtime installation; `tools/install_mcp.py:1064` creates the venv. Neither installer validates the shared-root setting. Its refusal occurs in `tools/dayz_mcp/server_cli.py:177` when `shared_root()` is consulted.
- **Fix:** Separate the relative-root refusal from the selector-conflict guarantee. Keep this correction within documentation scope.

**F3 — P3 — Writer-contention diagnostics are described too universally.**  
[tools/README-mcp.md:64](/C:/Users/guill/dzmcp_gauntlet/g5_docs/ws/tools/README-mcp.md:64) promises `state_root_writer_busy` for a second writer.

- **Scenario:** A daemon holds the `alpha` root lease; start another daemon for `alpha`, using another port and a valid keyfile.
- **Actual:** The daemon logs `DAEMON: state root already has a writer` and exits **75**, without emitting `state_root_writer_busy`. Reproduced with acquisition failure mocked.
- **Evidence:** `tools/dayz_mcp/daemon.py:1562`. The named error is raised by coordinated state activation at `tools/dayz_mcp/daemon.py:405`.
- **Fix:** Distinguish daemon exit/log diagnostics from activation exceptions and migration-scan errors. The recommended operator action remains valid.

## SPEC COVERAGE

| Specification bullet | Status |
|---|---|
| English `Independent instances` section immediately after Install | **done** |
| Token grammar, reserved `default`, per-instance state/port/keyfile/registration/game path | **done** |
| Shared box admission, build locks, host-config journal, absolute shared root | **done** |
| Separate tools trees and both installer commands | **done** |
| Selector environment behavior and conflicts | **wrong** — F1 |
| Legacy adoption, quiescence blockers/non-blockers, receipt and recovery action | **done**; diagnostic distinctions need F3 clarification |
| Lifetime writer lease and second-writer behavior | **wrong** in diagnostic specificity — F3 |
| Required operator error-code list and actions | **done** |
| Spanish H16 row, four columns, required observable criteria | **wrong** in its additional relative-root guarantee — F2 |
| Existing CHANGELOG line preserved; documentation pointer added | **done** |
| No production-code changes; necessary PROJECT-MAP size adjustment | **done** |
| `tests.test_docs_truth` passes | **done** — independently ran 22 tests, 3 skipped |

## GATE GAP

No tests were added. `tests.test_docs_truth` does not validate the new section’s selector semantics, H16’s effect ordering, or mode-specific contention diagnostics. All three findings coexist with its green result.

The supplied fast-tier gate establishes no new failures; it does not establish that these new documentation claims match executable behavior.

## PREMISE

- The supplied patch omits the current tree’s H16 and PROJECT-MAP changes. I reviewed those directly against `base_g5final`.
- `REPORT.md` reports zero fast-tier failures; the authoritative orchestrator output reports two baseline failures, zero new. These are different measurements.
- The report says the specification requires new tests. The supplied specification requires the existing documentation gate to pass; it does not require new tests.
- Environment-only selection is unsupported by the underlying implementation. Accurate documentation must explain that restriction.

## NOT VERIFIED

No live installation, registration, migration, concurrent daemon launch, build or in-game session was performed. Effect-order and contention probes intercepted effects or mocked lock acquisition.

The project-venv documentation gate passed. Python 3.11 and the fast tier were not rerun; their results remain orchestrator-supplied. No files were modified or committed.

