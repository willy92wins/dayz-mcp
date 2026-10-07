# g5 (independent instances): DZ-R9 rigorous data audit, summary

Data-critical batch (per-instance state roots, store migration, one writer per root, installer transactions on
the hosts' MCP configs). The audit followed the `rigorous-data-audit` skill; every file referenced here is in this
directory.

## Step 1: mechanical census (gpt-6.1-sol)
`r9/1_MECHANICAL.md`: path-naming matrix, cleanup symmetry, entry points, flags and environment. M1-M11.

## Steps 2-3: angle auditors and independent verification
- Auditor A (GLM-5.3-Flash, persistence/migration/recovery): `r9/2_AUDITOR_A.md`. Auditor B (GLM, exclusion,
  concurrency, selection): `r9/2_AUDITOR_B.md`.
- Independent verifier (gpt-6.1-sol, fresh context, bare claims): `r9/3_VERIFY_A.md`, `r9/3_VERIFY_B.md`.
- Orchestrator self-sample (2/7 of A, read in the code) and one experiment: a lock file opened with
  FILE_SHARE_DELETE was deleted while held and a second process locked a fresh file of the same name (two holders).
- Deduplicated table: `r9/R9-TABLE.md`. Fixed: A1, A2 (P1 host-config journal wedges), A3, A6, B2, B3 (P2), A4, A5,
  A7, M2-M5 (P3). Dropped after verification: B1, B4.

## Step 4: implementer-grade cross-actor pass (GLM)
`r9/4_IMPLEMENTER_GRADE_C.md`: 10 findings, all already in the table (C1=B3, C2=A7, C3=A4, C4=A5, C5=M2/M4, C6=A3,
C7=M5, C8=M11/A6, C9=A2, C10=B2/M9): the audit converged.

## Step 5: fixes (Grok 4.7 implemented, gpt-6.1-sol reviewed every round)
`fixes/`: g5r9_journal (A1, A2, A3; approved r3 after a consolidation: one cross-process installer lock without
FILE_SHARE_DELETE and a journal per registration identity, verified by the orchestrator with two real processes:
the contender gets registration_busy after its wait and cannot delete the held file), g5r9_locks (A6, A7, B2, B3;
approved r2), g5r9_selection (M2-M5, A4, A5; approved r3). The three batches were composed and gated together.

## Step 6: re-audit subset
- gpt-6.1-sol mechanical re-check `r9/6_MECHANICAL_RECHECK.md` (N1-N7) and two GLM angle auditors:
  `r9/6_AUDITOR_D_EXCLUSION.md` (P3 only: converged) and `r9/6_AUDITOR_E_PERSISTENCE.md` (14 crash boundaries;
  E1/E2 were two crash states that still wedged the shared host-config journal).
- One loop-back round, g5r9_step6 (approved r1): staging name for the first host-config publication and an empty
  journal directory treated as no journal (E1, E2/N5), error codes reach the caller by name (N4), finite lock wait
  (N6), relative journal root refused (D1).

## Owner decisions folded into this PR
- `install-mcp.ps1 -Register` delegates the registration to `install_mcp.py --register` (one transactional path):
  batch g5_ps1 (approved r2; the orchestrator replaced a test's hard-coded interpreter with sys.executable).
- Operator documentation `tools/README-mcp.md` `## Independent instances` and product-spec H16: batch g5_docs
  (approved r2).

## Integration fixes by the orchestrator (found by the whole suite, which the gauntlet's fast tier skipped)
- The worker request keysets: g5's optional selector keys combined with main's `project_mod_override` variant.
- `mcp_capture.py`: g5's selection check first, then main's frame-cap metadata.
- `install-mcp.ps1`: `-Instance`/`-GamePath` arguments only when non-empty (`[string]::IsNullOrEmpty`).
- `process_lifecycle.py` imports `box_admission` at module level, so the server's production import closure is
  preloaded (tests.test_server_freshness).

## Residual backlog (P3)
N1 (the CPython download lock of `build_native_launcher.py` waits 10 s blocking), N3 (doctor, stdio bridge,
capture and knowledge pack do not reject a conflicting environment selector the way the daemon does), N7 (a
failed `open_osfhandle` leaks a native handle), D3 (a legacy writer started after the start scan: closed in practice
by retiring the legacy tree at promotion), E3/E4 (named refusals that need an operator to clear a journal), M9, M11.

## Step 7: in game
Not run yet. It is the joint v11 promotion: both instances upgraded together, the legacy 1.30 tree retired, the
1.30 store adopted, and the checks in `PROMOTION_V11.md` step 7.
