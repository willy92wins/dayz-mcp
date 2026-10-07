# g5fix (named-instance profile folders): review loop and DZ-R9 step-6 re-audit

Found in game on 2026-10-07 during the v11 promotion (instance `130`, DayZ 1.30 Experimental; inbox
fb-20261007-135819-39b8). The owner decided to merge g5 (#216) and fix this in a follow-up batch.

## Specification
gpt-6.1-sol verified the finding against main `bbda2e9` and wrote the binding specification (`SOL_G5FIX.md`, `SPEC_G5FIX.md`). It confirmed F1 (P1, provisioning), F2 (P1, data-critical: the close of a named run skipped the
logout wait), F3 (P1, `file_matches` could read the legacy client profile) and F4-F7 (P2: `logs_since` / `log_matches`,
attestation, artifacts and client diagnosis, VPP preflight).

## Review loop (`loop/`)
- Round 1: Grok 4.7 implementer. The gate passed and gpt-6.1-sol required changes (3 P2).
- Round 2: Grok 4.7 implementer. The gate failed on existing fixtures and gpt-6.1-sol required changes (2 P2, plus the reseal, which is a deployment step).
- Round 3: orchestrator consolidation (`directive_r3.md`), GLM-5.3-Flash implementer. The gate passed and gpt-6.1-sol **approved**: the project check now fails closed and the fixtures are adapted with all 320 original assertions unchanged.

## DZ-R9 step 6 (`r9/`)
- Mechanical re-check, gpt-6.1-sol (`MECHANICAL.md`): one P3. `tools/gen-project-map.ps1` still composes legacy profile anchors in the generated PROJECT-MAP. It is pre-existing and documentation only.
- Auditor F (`AUDIT_F.md`), GLM-5.3-Flash, close and logout wait, concurrent instances. No P0-P2. Two P3:
  - F1: a refused anchor gives an honest `graceful=false`, but the close does not say which cause refused it.
  - F2: the log readers now depend on reading the launcher registry, and a transient failure makes them fail closed.
  - The orchestrator self-sampled F2 in the code: `_close_project_policy` returns `None` on any exception.
- Auditor G (`AUDIT_G.md`), GLM-5.3-Flash, provisioning, credentials and fail-closed paths. No P0-P2. Two P3:
  - G1: a lost race between two concurrent `prepare` calls is reported as `instance_config_missing`.
  - G2: the typed error contract rests on the caller's blanket handler.
  - G also re-verified the sealed-module lock hashes.
- The audit converged. The P3s go to the backlog with their repros.

## Local checks on the composed branch (main `f8aad74` plus this change)
- Batch modules plus the four adapted fixture modules: 468 tests, OK (1 skipped).
- Fast tier: 6009 tests. The only failure, the PROJECT-MAP CHANGELOG size claim, is fixed in this branch.
- `write_packaged_modules_lock.py --check`: ok.

## Not done here (deployment, DZ-R9 step 7)
- Launcher reseal: `server_cli.py`, `dayz_test_worker.py` and `dayz_test_attestation.py` are sealed. The live trees stay on the previous main until the reseal.
- In-game check on instance `130`, after the reseal. Both role folders must be created at the first launch. The close must observe a connected player's logout and read fresh termination lines. `file_matches`, `logs_since` and `log_matches` must read the named folders.
