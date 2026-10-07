# gpt-6.1-sol review, g5_ps1, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F4 — P2 — The new quoting regression test depends on the owner’s external venv.**  
`tools/tests/test_install_ps1_delegate.py:28` hard-codes Guillermo’s development Python; `:250` launches it instead of the interpreter running the tests.

- **Executable scenario:** On a clean Windows CI runner without that external directory, run from `tools/`:
  ```text
  python -B -m unittest tests.test_install_ps1_delegate.InstallPs1DelegateTest.test_trailing_backslash_reaches_native_python_argv
  ```
- **Wrong output:** Launching the missing executable returns `ExitCode=null`; the probe throws at `:236`, and the test fails at `:259` with `AssertionError: 1 != 0`.
- **Impact:** Both Windows CI tiers create their venv inside the checkout and discover this test (`.github/workflows/tests.yml:62`, `:78`, `:109`, `:119`). The local gauntlet passes because the external interpreter exists on this machine.
- **Verified:** Reproduced the missing-executable path through the actual launcher and consumer check entirely in memory: null exit code, then process exit 1. Full test execution requires writable temporary files.
- **Fix:** Use `sys.executable`, preserving the native argv-consumer assertion.

Previous **F1–F3 are fixed**: executable candidates now satisfy the pin contract; trailing-backslash arguments survive native launching; the documentation-size check passes.

## SPEC COVERAGE

| Specification bullet | Status | Evidence |
|---|---|---|
| Preserve preparation, selector validation and replacement decision; delegate registration transactionally | **done** | Argument builder at `tools/install-mcp.ps1:840`; production delegation at `:1091`; Python reaches `register_transaction` at `tools/install_mcp.py:1768`. |
| Remove direct registration mutations and rollback seam | **done** | No executable `mcp add/remove` calls remain. The legacy validation flag refuses with `registration_rollback_seam_removed`. Printed commands remain. |
| Propagate Python exit code and named error token | **done** | Actual result-handling function reproduced exit 2 and a standalone `registration_busy` token. |
| Preserve accepted flags or refuse by name; preserve print-only default | **done** | Existing options are forwarded at `tools/install-mcp.ps1:848`; preparation handles knowledge-pack behavior; print-only commands remain. |
| Required delegation, failure-propagation and early-validation tests | **wrong** | Required cases exist, but the added F2 regression test breaks clean CI because of F4. |
| Update installation documentation | **done** | `tools/README-mcp.md:18` and `:26` describe delegation, pinning and transactional registration. |

## GATE GAP

The gate runs on the owner’s workstation, so it misses F4’s external-path dependency.

The delegation tests also substitute the Python process and use synthetic PE files. They cannot prove that discovered real CLI executables successfully complete pinning, absence probes and registration through the combined installer path.

## PREMISE

- “Tests … must fail on **this tree**” is contradictory; regression tests should fail on the pre-change tree and pass on the implementation.
- The supplied patch omits the `PROJECT-MAP.md:84` correction. The current tree contains it, and its size check passes.
- I used the orchestrator’s supplied gate results as authoritative, including the two unchanged baseline failures.

## NOT VERIFIED

- No real installation, CLI registration, host mutation or DayZ process management was performed.
- The full delegate module could not complete locally: temporary-file creation is denied; its unmodified PowerShell invocation also encounters execution-policy refusal.
- Read-only checks passed: documentation size, absence of direct mutation calls, native argv preservation, pin validation for the available Claude executable, early validation, legacy-flag refusal, and error/exit propagation.
- CI was inspected, not executed. No files were modified.