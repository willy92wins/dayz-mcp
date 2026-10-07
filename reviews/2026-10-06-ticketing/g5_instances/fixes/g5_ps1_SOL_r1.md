# gpt-6.1-sol review, g5_ps1, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — The delegate always supplies an unsupported Codex executable.**  
`tools/install-mcp.ps1:983` resolves **`codex.cmd`**, and `:988` passes it through `--codex-exe`. The new branch at `tools/install_mcp.py:1724` pins that path, but `_resolve_pin_cli` rejects non-`.exe` paths at `:420`.

- **Scenario:** Valid preparation, native `claude.exe`, working `codex.cmd`, and absent registrations; run `install-mcp.ps1 -Register`.
- **Wrong output:** `installer_cli_not_native_exe`; registration never reaches the transaction.
- **Verified:** With only runtime preparation mocked, the actual pinning code produced that error and `register_transaction` had **zero calls**.
- **Fix:** Supply a verified native `codex.exe` path. Add a test connecting the generated arguments to Python’s actual pin-validation contract.

**F2 — P2 — Native argument quoting corrupts paths ending in a backslash.**  
`tools/install-mcp.ps1:776` now launches Python through `Invoke-NativeRegistrationCommand`. Its quoting at `:539` wraps arguments in quotes without escaping trailing backslashes for the native argument parser.

- **Scenario:** `install-mcp.ps1 -Register -Instance 130 -GamePath 'C:\DayZGames\DayZ\' -NoSupervised`.
- **Wrong output:** Python receives:
  ```text
  --game-path 'C:\DayZGames\DayZ" --no-supervised'
  ```
  The game path is corrupted and `supervised` remains `True`.
- **Verified:** Extracted the actual argument builder, applied the wrapper’s exact quoting, and passed the result through `cmd.exe` to native Python and `install_mcp.parse_args`. Both incorrect values were reproduced without installing or registering anything.
- **Fix:** Preserve arguments using native Windows argument encoding; test trailing-backslash paths against a native Python argv consumer.

**F3 — P3 — The changelog addition breaks the documentation-size gate.**  
`PROJECT-MAP.md:84` still claims 45 KB; the addition at `CHANGELOG.md:19` increases the file to **47,246 B / 46.1 KiB**.

- **Scenario:** From `tools/`, run:
  ```text
  python -B -m unittest tests.test_docs_truth.ProjectMapSizesDocsTest
  ```
- **Wrong output:** `CHANGELOG.md: claims 45 KB, file is 47246 B (46.1 KiB)`.
- **Verified:** Current tree fails; the same test loaded from `base_g5final` passes.
- **Fix:** Refresh the size claim.

F1 and F2 block approval.

## SPEC COVERAGE

| Specification bullet | Status |
|---|---|
| Preserve preparation, selector validation and replacement decision; delegate registration with matching arguments | **Wrong:** preparation and decisions remain, but the executable contract and argument transport fail (F1/F2). |
| Remove direct host add/remove calls and the rollback seam | **Done:** direct calls and `Undo-DayZMcpClaudeRegistration` are removed; the old validation switch receives a named refusal. |
| Propagate Python’s exit code and print its error token | **Done:** an in-memory invocation probe preserved exit 7 and printed `registration_journal_invalid`. |
| Preserve accepted flags and print-only default | **Wrong:** print-only command construction remains; F2 breaks valid flag combinations. |
| Add default/named invocation, no-direct-call, nonzero/token and `ValidateOnly` tests | **Done structurally:** all cases exist, but the assertions accept F1 and omit F2. The no-direct-call test passed; direct `ValidateOnly` execution exited 0. |
| Update the install documentation | **Done:** `tools/README-mcp.md` describes delegation and named errors. |

## GATE GAP

The new tests substitute a **`.cmd` seam**, which does not validate Python’s executable contract or exercise native Python argument parsing. They explicitly expect `codex.cmd` at `tools/tests/test_install_ps1_delegate.py:133`, encoding F1 as the expected result.

The named-instance test uses a path without a trailing backslash (`:145`). Existing registration probes also replace the delegate. These tests can pass while production registration fails.

## PREMISE

- “Each must fail on this tree” contradicts an acceptance-test objective. I interpreted this as **fail on the unmodified tree, pass after implementation**.
- The supplied patch compares against `base_g5final`; an `origin/main` comparison was not independently available.
- The staging-ignore test reproduced **Git exit 128**, because this directory has no Git metadata. Its ignore rule exists and is unchanged in the baseline; this is not evidence of a new implementation defect.
- `REPORT.md`’s clean gate claim is contradicted by the measured documentation regression.

## NOT VERIFIED

- No real installer, registration mutation, kill/recovery experiment, or DayZ operation was executed.
- `python -B -m unittest tests.test_install_ps1_delegate tests.test_install_mcp` could not provide valid suite evidence: the sandbox has no writable temporary directory, and the available Python lacks dependencies such as `anyio`. PowerShell policy also affected a test invocation.
- Full fast-tier validation was not rerun. No files were modified and no commit was created.

