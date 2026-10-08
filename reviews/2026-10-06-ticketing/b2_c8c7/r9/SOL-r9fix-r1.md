VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — Malformed duplicates leave an ambiguous measurement visible.**  
`tools/dayz_mcp/process_lifecycle.py:266` counts duplicates only after invalid entries have been discarded at `:245`, `:255`, or `:257`. Consequently, a valid observation survives when another entry names the same run ID but is malformed. F2 requires that duplicated run ID to be dropped entirely.

Executable reproduction, from `tools/`:

```python
from dayz_mcp.process_lifecycle import _storage_observations_from_payload

rid = "11111111-1111-4111-8111-111111111111"
good = dict(run_id=rid, storage_rotated=False,
            storage_backup=None, storage_reset_notice=None)
print(_storage_observations_from_payload(
    [good, dict(good, storage_rotated="broken")]
))
```

**Expected:** `[]`.  
**Actual:** `[good]`, publishing a measured `false` for an ambiguous ID.

I also reproduced this through the real `RunManifestStore._load`, supplying JSON through an in-memory path: the valid run remained visible, but the ambiguous observation survived. Reversing the copies or replacing the malformed copy with `{"run_id": rid}` produces the same defect.

**Suggested fix:** identify repeated non-empty string run IDs across the original dictionary entries, regardless of whether their other fields validate. Exclude every observation bearing those IDs before trimming. Add mixed valid/malformed duplicate regressions. The report acknowledges this behavior; it does not satisfy the unconditional duplicate rule.

## SPEC COVERAGE

| Requirement | Status |
|---|---|
| F1: refused launch records nothing; provisional storage fields remain null | **done** |
| F1: settled EXITED row remains unknown, adds no observation, preserves refusal code | **done** |
| F1: lifecycle and failed public-result regressions using a real blocked classification | **done** |
| F2: missing/non-list advisory value loads empty | **done** |
| F2: malformed entries degrade individually | **done** |
| F2: extra keys are ignored and not copied | **done** |
| F2: any repeated run ID is dropped entirely | **wrong** — mixed valid/malformed duplicates survive |
| F2: retain newest 32 entries after filtering | **done** |
| F2: run-row storage validation remains strict and unchanged | **done** |
| F2: required five regression cases retain visible runs | **done**, but duplicate coverage uses two valid copies |
| F3: existing-run calls return three null fields on successful and failed terminals | **done** |
| F3: creating launches retain existing success/failure behavior and operation check | **done** |
| F3: creation-positive, client-reattach and stop regressions | **done** |
| Scope: pinned modules, native launcher sources and excluded backlog remain unchanged | **done** |
| CHANGELOG constraint | **done** — unchanged |

The two pinned modules and `packaged-modules.lock.json` match the baseline by SHA-256.

## GATE GAP

The gate misses **F1 above** because the duplicate test supplies two fully valid entries. It never combines a valid observation with a malformed observation bearing the same ID.

The new reattach/stop tests exercise `_execute_request` with fabricated worker terminals and explicitly supplied `expected_run_id`. They would miss a regression in the outer tool’s argument propagation or actual worker behavior. Independent in-memory checks passed for creating-success and existing-run success/failure across client, offline and stop modes.

## PREMISE

The implementer’s interpretation that duplication applies only to “surviving entries” weakens the specification’s explicit “dropped entirely” rule.

The supplied fast-tier PASS means **zero new failures**, with two failures still present; it is not an entirely green suite. Their identities were not supplied.

The diff also contains an empty, ignored `tools/approved-launchers.lock` runtime artifact. It contributes no functional fix and should stay outside the merge payload.

## NOT VERIFIED

- No full unittest rerun here: the sandbox is read-only, and these fixtures create temporary files. I used Python 3.14 with `-B` for write-blocked, in-memory checks.
- Baseline failure of every new test was not independently rerun.
- Native launcher execution, real worker-process integration, DayZ/Steam behavior and slow-tier tests were not exercised.
- No files were modified and no commit was created.

