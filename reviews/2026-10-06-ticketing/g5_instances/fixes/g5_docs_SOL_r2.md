# gpt-6.1-sol review, g5_docs, round 2 (unedited)

VERDICT: APPROVED

## FINDINGS

Previous **F1, F2 and F3 are fixed**. No P1/P2 findings remain.

**F4 — P3 — Environment matching omits Python path normalization.**  
`tools/README-mcp.md:60` says environment values must equal the flags exactly.

- Scenario: set `DAYZ_MCP_GAME_PATH=C:/Games/DayZ`, unset the other selector variables, then parse:
  `python install_mcp.py --register --instance alpha --game-path C:/Games/DayZ --port 8766`.
- Actual: exits **2**, reporting `instance_environment_conflict`, despite identical environment and flag text. Reproduced through `install_mcp.parse_args`, without installation.
- Mechanism: `tools/dayz_mcp/server_cli.py:51` normalizes the flag to `C:\Games\DayZ`; `:458-460` compares that normalized value with the environment.
- Suggested correction: specify that Python compares against the **normalized parsed game path**. Canonical backslash paths already work.

**F5 — P3 — Report understates existing gate failures.**  
`REPORT.md:24` records `failures=0`; the orchestrator’s `r2/gate.txt` records `failures=2 new=0`. Correct the copied result. Both report `GAUNTLET_GATE: PASS`; this does not change the regression verdict.

## SPEC COVERAGE

| Specification bullet | Status |
|---|---|
| README section after Install; English, paragraphs, lists and code spans | **done** |
| Token grammar, reserved `default`, separate state/port/keyfile/registration/game path | **done** |
| Shared physical admission, build locks, host-config journal and absolute shared root | **done** |
| Separate tools trees; Python and PowerShell creation commands | **done** |
| Selector/environment behavior and conflicts | **done**, with the P3 normalization qualification above |
| Legacy adoption: blockers, non-blockers, receipt, recovery action and settled-root protection | **done** |
| Lifetime writer lease and second-writer diagnostics | **done**; daemon exit/log and activation exception are distinguished |
| Required operator error codes and remedies | **done** |
| Spanish H16 row, observable criteria and four-column layout | **done** |
| Existing CHANGELOG line retained; README pointer added | **done** |
| Documentation-only scope and PROJECT-MAP size adjustment | **done**; implementation files match the base |
| `tests.test_docs_truth` passes | **done**; independently ran 22 tests, **OK, skipped=3**, using the project venv |

## GATE GAP

No new tests were added. `tests.test_docs_truth` does not assert the independent-instance prose or H16 semantics. Incorrect selector instructions, migration blockers or contention diagnostics could therefore pass the gate—as the normalization wording demonstrates.

## PREMISE

The supplied `DIFF.patch` is incomplete relative to the current tree: it omits the `product-spec.md` and `PROJECT-MAP.md` changes. Direct comparison with `base_g5final` confirms both are present.

The specification requires the existing docs tests to pass; it does not require new tests, contrary to `REPORT.md:30`.

## NOT VERIFIED

- No installation, registration, live daemon startup, migration or in-game sequence was performed.
- Contention diagnostics were verified with lease acquisition mocked; this does not prove cross-process locking.
- Python 3.11 and the full fast tier were not independently rerun; their results come from the orchestrator.
- Initial system-Python testing lacked `anyio`; the project-venv rerun passed.
- No files were modified.

