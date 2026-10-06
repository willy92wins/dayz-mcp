# gpt-6.1-sol review, g3_launch_contract, round 4 (unedited)

VERDICT: APPROVED

## FINDINGS

No remaining P1/P2 findings or new actionable defects within the binding round-4 scope. F4 and F13 are closed.

Independent public-adapter probes used the specified request:

`project="ExampleMod", mode="server", preflight=true, base_mods=[], extra_mods=["@DayZ_MCP"]`

| Evidence state | Reconstructed round 3 | Current tree |
|---|---|---|
| Valid runtime and unchanged roots | `succeeded / null` | `succeeded / null` |
| Missing `build_source_basename` | `succeeded / null` | `failed / runtime_policy_invalid` |
| Replaced mod root | `succeeded / null` | `failed / invalid_dayz_test_path_authority` |
| Replaced dev, default-source or mission root | `succeeded / null` | `failed / invalid_dayz_test_path_authority` |

These probes used real parsing/accreditation code with mocked filesystem observations. They also checked that roots remained pinned during descendant opens and that opened handles were subsequently closed.

## SPEC COVERAGE

Coverage follows the binding round-4 closure directive.

| Requirement | Result |
|---|---|
| F13: validate sealed root volume/file identities before descendants | **Done** — `tools/dayz_mcp/request_path_authority.py:604` pins roots through `_open_sealed_root()`. |
| F13: restore `dev_root` and `default_source` validation | **Done** — both are included at `request_path_authority.py:605`. |
| F13: retain runtime-resolved absolute paths and pinned-root accreditation | **Done** — descendants use the selected pinned roots at `request_path_authority.py:609`; public preflight calls this helper at `dayz_test_tool.py:2365`. |
| F13: public refusal and regression | **Done** — replaced-root probes refuse correctly; regression at `tools/tests/test_launch_contract_c561_31d2_2837.py:1239`. |
| F4: one closed runtime-document parser, including required/unknown keys and semantics | **Done** — `tools/dayz_mcp/dayz_test_worker.py:267`. |
| F4: host accessor and bootstrap share that parser | **Done** — `native_bundle.py:1088` and `tools/native-launchers/dayz-test-v1/src/app_main.py:200`. |
| F4: preserve public `runtime_policy_invalid` | **Done** — `dayz_test_tool.py:2307`; missing-key regression independently passed. |
| F4: shared parser resides in an already packaged module; regenerate lock | **Done** — worker packaging at `tools/build_native_launcher.py:65`; independent lock check passed. |
| F4: acceptance/refusal parity regression | **Done** — regression at `test_launch_contract_c561_31d2_2837.py:1179` passed; another **52 expected document decisions** agreed across both consumers. |
| Preserve hang fix, F3, F7, F9, F10, F11 and F12 | **Done within inspected delta** — no reversal identified; transaction tests remain byte-identical to `base3`. |
| Standalone transaction suite finishes | **Done locally**, but sandbox setup errors prevented a pass. Implementer reports an isolated successful run. |
| `ROUND 4 FIXES` report | **Done** — `REPORT.md:112`. |

## GATE GAP

The new runtime-equivalence test covers only three documents; agreement alone could conceal a shared semantic error. The additional expected-outcome probes broadened that coverage.

The gate and mocked probes do not establish behavior under concurrent real NTFS junction/ancestor replacement or native handle failures. They also do not prove deployed-launcher compatibility.

## PREMISE

The supplied `r4/DIFF.patch` still omits the workspace’s `product-spec.md` changes at lines 146 and 148. Integrate those current-tree changes as well.

Source approval is separate from deployment: the combined launcher still requires resealing.

## NOT VERIFIED

- Independently passed two F4 regressions, 52 runtime-document probes, six differential public-adapter scenarios, and the packaged-source lock check.
- The transaction module finished, but temporary-directory creation failed under the read-only sandbox. The supplied full gate was not independently reproduced.
- No live reseal, daemon contact, DayZ launch, deployed-PBO verification, or live occupancy/storage checks.
- No files modified, commit created, or review artifact written.