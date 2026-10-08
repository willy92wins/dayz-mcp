# gpt-6.1-sol review, b3_dbe0, round 1 (unedited)

VERDICT: APPROVED

The source change is safe to merge. Deployment still requires reconciliation with #195 and resealing.

## FINDINGS

**F1 — P3 — Self-junction test can accept the old traversal.**  
`tools/tests/test_pack_only_junctions.py:75` checks only that the child prints `False` within five seconds.

Executable counter-scenario: enumerate a self-junction without assets until directory listing raises a path-limit `OSError`; if the interpreter suppresses that error and finishes within five seconds, the old predicate prints `False` and satisfies the assertion despite following the junction. A read-only probe executing the actual baseline predicate with this modeled filesystem returned `False` after **124 listings**; the new predicate returned `False` after **one**. This was a modeled control, not a physical junction reproduction.

Add a child-process assertion that the junction path is never enumerated, while retaining the timeout. The outside-junction test independently catches restoration of the original scanner, so this is nonblocking.

No P1/P2 implementation defects found.

## SPEC COVERAGE

| Specification requirement | Status | Evidence |
|---|---|---|
| Fix the recursive-enumeration vulnerability without promising a particular exception | Done | Iterative traversal at `tools/dayz_mcp/pack_only.py:41`; report’s pre-fix failure claim is overstated—F1. |
| Implement traversal in the shared helper; delegate `_default_has_assets` | Done | `pack_only.py:33`; `dayz_test_worker.py:634`. Delegation probe passed. |
| Inspect entries without following links; skip junctions and symlinks | Done | `pack_only.py:46–54`, including no-follow tag inspection at `:24`. |
| Preserve non-link reparse points, including cloud placeholders | Done | Name-surrogate bit classification at `pack_only.py:19–30`; placeholder fixture present. |
| Preserve suffix matching and wrapper chain | Done | `pack_only.py:14`, `:55`, `:61–72`. |
| Preserve explicit `pack_only=True` short-circuiting | Done | `pack_only.py:63`; read-only probe confirmed no scan. Worker expression retained at `dayz_test_worker.py:732`. |
| Preserve surfaced `OSError` → `build_source_unavailable` | Done | `dayz_test_worker.py:635–636`; error-mapping probe passed. |
| Retain source-link authorization separately | Done | Authorization code unchanged; scanner adds no authorization decision. |
| Windows self-junction without assets, bounded process | Done fixture; wrong regression oracle | Five-second child timeout present; F1 limits what the result proves. |
| Separate normal `.p3d`, outside-link, placeholder classification cases | Done | `test_pack_only_junctions.py:78`, `:86`, `:102`. |
| Reconcile with #195 before resealing | Missing / deferred | Draft absent from this batch. |
| Reseal changed worker; no PBO | Reseal missing; no PBO done | Packaging lists and lock updated consistently. Resealing remains an integration prerequisite. |

## GATE GAP

The supplied gate proves no new failures relative to its baseline, not a clean suite. It cannot establish:

- Reliable rejection of pre-fix cyclic traversal under error-suppressing interpreters.
- Actual filesystem symlink behavior or dehydrated OneDrive placeholder behavior.
- Acceptance and execution of a newly sealed native bundle.

Locally, **three packaging closure/list-consistency tests passed**, and `write_packaged_modules_lock.py --check` passed.

## PREMISE

The native launcher consumes the worker’s flag; delegation does not establish PowerShell scanner parity.

`REPORT.md:35` calls “no reseal” specified behavior. The specification requires resealing after reconciliation with #195. That work is deferred, not waived. Neither the launcher executable nor its closure manifest exists in this checkout.

## NOT VERIFIED

The fixture suites could not run successfully in this read-only sandbox: temporary-directory creation failed. The orchestrator’s supplied Windows results remain the available physical-fixture evidence.

Not verified: physical pre-fix reproduction, #195 integration, rebuilt/installed bundle, native build execution, real cloud placeholders, or in-game behavior. No files were modified.

