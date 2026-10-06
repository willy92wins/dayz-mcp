# gpt-6.1-sol review, g3_launch_contract, round 3 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F4 — P2 — Host and worker runtime validation still disagree.**  
`tools/dayz_mcp/native_bundle.py:1121`; `tools/native-launchers/dayz-test-v1/src/app_main.py:206`

The shared semantic validator does not cover the bootstrap’s closed runtime schema. The accessor defaults missing `build_source_basename` to `None`; the bootstrap requires that key.

- **Executable scenario:** Supply a sealed runtime project with valid absolute paths and all three required mission aliases, but omit `build_source_basename`. Call `project="ExampleMod", mode="server", preflight=true, base_mods=[], extra_mods=["@DayZ_MCP"]`.
- **Measured wrong output:** Public preflight returns `status="succeeded", error_code=null`. `_worker_runtime()` consuming the identical JSON raises `worker_runtime_invalid`.
- **Fix:** Share complete runtime-document parsing and validation, including required/unknown keys, between the bundle accessor and bootstrap. Preserve the public `runtime_policy_invalid` refusal.

**F13 — P2 — Exact-path accreditation bypasses sealed root identities.**  
`tools/dayz_mcp/dayz_test_tool.py:2365`; `tools/dayz_mcp/request_path_authority.py:541`

The replacement accreditation calls `_accredit_absolute()` without first opening and validating the sealed roots. `_open_descendant()` checks descendants but assumes the root has already been validated. Preflight also no longer accredits `dev_root` or `default_source`.

- **Executable scenario:** Seal `C:\mods` with identity `(volume=1, file_id=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA)`. Replace that directory, preserving `@ExampleMod` and `@DayZ_MCP`, so its observed identity becomes `(1, BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB)`. Keep the runtime and mission valid. Call the same server preflight above.
- **Measured wrong output:** Public preflight returns `succeeded / null`. The probe opens only the two mod descendants and mission descendant. Calling the existing `_open_sealed_root()` against the same filesystem observations correctly raises `invalid_dayz_test_path_authority`.
- **Fix:** Restore sealed root and source identity validation while retaining runtime-resolved absolute-path accreditation. Keep the validated roots pinned during descendant checks.

Both scenarios were reproduced through the public adapter with real policy/accessor/accreditation code and mocked filesystem observations.

## SPEC COVERAGE

Coverage below follows the binding round-3 closure scope.

| Requirement | Result |
|---|---|
| HANG: fix production code; retain original transaction tests | **Done by inspection and supplied gate evidence.** Transaction tests are byte-identical to `base3`; independent execution was blocked by temporary-directory permissions. |
| F3: resolve effective paths before accrediting them | **Done**, but the replacement introduces F13. The original `@Missing` scenario now fails with `invalid_dayz_test_path_authority`. |
| F4: complete host/worker runtime validation agreement | **Wrong.** Alias/path semantics are shared; bootstrap schema requirements remain separate. |
| F7: authoritative box observation; unknown remains `null` | **Done.** Uses `session_status()["box"]`; foreign-process and unknown-scan regression passed. |
| F9: `unverifiable` precedence in every artifact order | **Done.** Independently reproduced both orders and the missing-entry variant. |
| F12: normalized original-directory comparison and closed public reason | **Done.** Regression and request-vocabulary tests passed. |
| F10: strict typing before equality/hashing/membership | **Done** for all four specified inputs; regression passed. |
| F11: update H11/H13 in existing row style | **Done** in the current tree; documentation regression passed. |
| Preserve previously closed findings and initialization correction | No reversal identified in the inspected changes; their earlier closures were not comprehensively re-audited. |
| Regression tests and `ROUND 3 FIXES` report | **Done** for the named corrections; tests miss F4’s schema mismatch and F13’s stale-root identity. |

## GATE GAP

The new F3 test uses unchanged sealed roots, so it cannot detect the missing identity check. The F4 test exercises invalid aliases through the accessor but never compares complete runtime-document acceptance against the actual bootstrap.

The supplied green gate therefore does not exclude either reproduced false preflight success.

## PREMISE

`r3/DIFF.patch` omits the `product-spec.md` changes present in this workspace and absent from `base3`. I treated the current tree as authoritative.

Approval to merge source would also remain separate from deployment: the installed launcher was not resealed.

## NOT VERIFIED

- Independently passed **22 tests**: 17 request tests and five round-3 regressions using the project venv.
- The transaction module could not reach its relevant checks because the read-only sandbox prevents temporary-directory creation. Its supplied gate result remains orchestrator evidence.
- No full-suite rerun, live launcher reseal, daemon contact, DayZ launch, deployed-PBO verification, or live occupancy/storage check.
- No files modified; no commit or review artifact written.
- Memory informed only the caution about mocked safety tests; findings use current source and probes.

