# gpt-6.1-sol review, g5r9_step6, round 1 (unedited)

VERDICT: APPROVED

## FINDINGS

No P1/P2 findings. The implementation addresses the specified recovery states and preserves complete `manifest.next` recovery.

## SPEC COVERAGE

| Requirement | Status | Evidence in this tree |
|---|---|---|
| E2/N5: staging publication, fragment cleanup, retain A2 | **Done** | `tools/dayz_mcp/host_config.py:963`, `:1012`, `:1244` |
| E1: remove empty journals; refuse unexpected entries | **Done** | `tools/dayz_mcp/host_config.py:1037` |
| N4: named CLI exceptions and safe wire codes | **Done** | `tools/dayz_mcp/server_cli.py:77`, `tools/dayz_mcp/bridge_errors.py:233` |
| N4: preserve `registration_lock_open_failed` | **Done** | `tools/install_mcp.py:1765`, `:1818` |
| N6: reject nonfinite or negative registration waits | **Done** | `tools/install_mcp.py:1248` |
| D1: reject relative journal roots before effects | **Done** | `tools/install_mcp.py:1482` |
| Required regressions | **Done** as test cases | `tools/tests/test_g5_r9_step6.py:93`, `:115`, `:137`, `:155`, `:202`, `:221` |
| Regenerate sealed-module lock | **Done** | Official `write_packaged_modules_lock.py --check` passed |
| Preserve PowerShell, registration protocol and lock implementation | **Done** | No corresponding implementation changes in the supplied diff |

The diff also includes an empty, ignored runtime file, `tools/approved-launchers.lock`. Exclude that artifact from the merge patch; it is unrelated to this batch.

## GATE GAP

The new fragment tests construct leftover files directly. Reverting `_persist_manifest` to write directly into `manifest.next` could leave those tests passing: they verify recovery, but do not enforce staging publication. Add fault injection during `_write_private` to verify the publication sequence itself.

The named-error test connects `build_run_request` directly to `_opaque_dayz_test_failure`. It would miss a future interception or replacement of the exception in the full MCP handler. The current handler forwards it through the formatter at `tools/dayz_mcp/server.py:3833`.

## PREMISE

“Each must FAIL on this tree” appears reversed: regression tests should pass on the modified tree and expose defects on the unmodified tree. The unexpected-file refusal is a preservation test and should pass on both.

The registration loader tolerates missing manifests by returning `None` (`tools/install_mcp.py:1357`); it does not itself remove an empty directory. This wording difference does not undermine the requested host-journal fix.

## NOT VERIFIED

- Filesystem-dependent recovery tests could not run successfully in this read-only sandbox. The broader attempt reported `110 tests`, with `91 errors`, `1 failure`, and `1 skipped`; temporary-directory creation was denied, and a PowerShell test hit execution-policy restrictions. This is **not** a reproduced implementation failure.
- Four write-free regression tests passed. Additional probes passed for invalid waits, relative host-root rejection before lock/provider calls, and unsafe wire-code suppression.
- Fail-on-baseline behavior was not executed.
- No real process-kill experiment, launcher reseal, or in-game verification. The supplied orchestrator gate remains the executable recovery evidence.

