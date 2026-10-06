# gpt-6.1-sol review, b2_c8c7, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P2 — The replacement correlation still hides real resets and can attribute another run’s reset to the caller.**

Locations: `tools/dayz_mcp/dayz_test_tool.py:1717`, `:1834`, `:1842`, `:1844`.

The implementation treats the single run ID added between two global status snapshots as this attempt’s ID. Those snapshots surround a transaction that acquires and releases its lease internally (`tools/dayz_mcp/native_launcher_transaction.py:708`, `:91`). Other sessions can create runs within that interval.

Executable scenarios, reproduced in memory against the actual `_execute_request`:

1. **False attribution:** initial status is `{"runs":[]}`. Another session creates an EXITED run with `storage_rotated=true`, backup `storage_1.modset-unrelated`, and the reset notice. This caller fails its build before creating any run; its valid terminal contains `error_code="build_failed"`, `run_id=null`, `cleanup_degraded=false`. Final status contains only the unrelated run.

   Actual public output:

   ```json
   {
     "status": "failed",
     "run_id": null,
     "storage_rotated": true,
     "storage_backup": "storage_1.modset-unrelated",
     "storage_reset_notice": "mission_world_and_character_reset"
   }
   ```

   The caller’s unavailable observation should remain null.

2. **Original reset still hidden:** initial status contains no runs. This attempt rotates storage, spawning fails, and cleanup succeeds. Another session also creates a run before the final snapshot. Status contains both new IDs, including this attempt’s measured reset. With the real failure-terminal shape (`worker_failed`, null run ID, successful cleanup), the tool returns all three storage fields as null because `len(fresh) == 2`.

3. **Transient initial status failure:** the first status read raises, but subsequent status reads contain the exact failed run with its true reset. `known_run_ids` remains null, preventing correlation; the result again contains three null storage fields.

**Suggested fix:** retain exact attempt-to-run correlation independently of the terminal’s cleanup run ID. A global set difference does not establish ownership.

Previous **F2 is fixed**: actual load → prune → persist → reload, exercised with in-memory filesystem I/O, retained the observation accessible by its original run ID.

Previous **F3 is fixed**: executing the actual changed pre-spawn branch with an injected `OSError` from `manifest.replace` returned structured `manifest_failed` without the previous `KeyError`.

## SPEC COVERAGE

| Specification requirement | Status | Evidence / limitation |
|---|---|---|
| Daemon/tool change; no PBO or worker reseal | done | Diff changes lifecycle, tool, tests and changelog. |
| Carry rotation into the provisional run before spawning | done | `process_lifecycle.py:3012`, `:3897`. |
| Optional validated metadata; preserve through settlement, cloning and reload; legacy unknown | done | Validation and serialization are present; bounded observations address terminal-row pruning. |
| Publish lifecycle status and extract by exact run ID | wrong | Both projections publish it, but cleaned failures infer the ID from unrelated global activity; F1. |
| Always include the three fields; null unknown, false measured nonrotation | done | `_compact_result` includes all three at `dayz_test_tool.py:1517`. |
| Preserve true resets after failures; read status before normalization loses identity | wrong | Explicit terminal IDs are preserved, but successfully cleaned failures still lose or misattribute observations; F1. |
| Retain history after acknowledgement; recover pending completed journals for retries | done | Acknowledgement preserves fields; recovery validates journal phase, seal and backup existence. Recovery resides in lifecycle, preserving the sealed storage module. |
| Do not claim acknowledgement/marker proves CE restored world contents | done | No such accreditation is introduced. |
| Legacy compatibility; downgrade may discard diagnostics without touching storage artifacts | done | Source supports compatibility; an actual downgrade was not executed. |
| Required regressions | missing | Listed cases are covered, but unrelated-run isolation only tests a run already present before execution. F1’s schedules are absent. |
| Leave H11 accreditation undefined | done | Explicitly deferred. |

## GATE GAP

The supplied gate passes with two baseline failures and zero new failures. It does not establish exact attempt correlation.

The F1 regression at `tools/tests/test_storage_reset_visibility.py:426` puts the unrelated run in the **initial** snapshot. It cannot detect unrelated runs created while this request waits or after its lease is released, multiple fresh IDs, or a failed initial status read followed by successful reads.

## PREMISE

The batch’s diagnosis and daemon/tool scope remain valid. H11 accreditation remains a separate unresolved acceptance question.

`REPORT.md:43` retains an outdated description implying the original observation is lost on reload; round 2’s durable observation list now addresses that loss.

## NOT VERIFIED

- No in-game launch, real daemon restart, or actual downgrade.
- Attempted `python -B -m unittest tests.test_storage_reset_visibility` from `tools/`. All 12 tests failed in setup because the read-only sandbox cannot create temporary directories; no test assertions ran.
- The focused in-memory reproductions ran successfully without filesystem writes. The orchestrator’s gate result remains externally supplied evidence.
- No files modified or commit created.