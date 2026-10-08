VERDICT: CHANGES_REQUIRED

## FINDINGS

### F1 — P1 — The strengthened oracle still accepts an impossible completed phase

`tools/tests/test_storage_rotation_model.py:506`, `:713`, `:394`

Completed journals are validated with the production validator, which accepts `storage_moved` for active journals. The oracle never restricts completed journals to `prepared` (abort) or `marker_published` (finished rotation). Closure also excludes completed journals.

**Executed failure scenario:**

- Schema-2 C, txid `"1"*32`, phase `storage_moved`.
- `new_seal="a"*64`, `old_seal=None`, project `DayZ_MCP`.
- `old_marker_state="absent"`, `old_marker_sha256=None`.
- Reserved D=`storage_1.modset-d3-legacy` contains O.
- Reserved K=`storage_1.modset-d3-legacy.marker.json` absent.
- W and J absent; M contains canonical N.

Calling `_check(nodes, original_world, None, SEAL_A)` **passes**. No row of the binding matrix permits C in `storage_moved`.

**Full-gate counterexample:** monkeypatch `_complete_journal` to call the original, read C, and—for completed rotations recording an absent original—atomically rewrite its phase:

```python
if (doc["old_marker_state"] == storage.MARKER_ABSENT
        and doc["phase"] == storage.PHASE_MARKER_PUBLISHED):
    doc["phase"] = storage.PHASE_STORAGE_MOVED
    storage._write_json_atomic(completed_path, doc, replace=True)
```

Run `test_depth_two_fixed_point_preserves_world_and_marker` with this wrapper.

**Wrong output observed:**

```text
Ran 1 test in 42.391s
OK
completed Q -> S mutations: 4701 gate_success: True
```

This leaves F1’s phase-validation requirement incomplete. It demonstrates a false pass in the mandatory gate, rather than an observed production recovery defect.

**Suggested fix:** independently validate completed-journal phases before applying closure equivalence. Add this atomic wrong-phase mutation as a negative control that must fail.

F2–F4 are closed: current-tree probes refuse F2/F3 with identical complete snapshots and zero mutations; F4 succeeds and retains the rotation result. Same-seal recovery keeps the journal project, and different-seal recovery publishes the caller’s marker. The reconstructed round-2 module fails those probes.

## SPEC COVERAGE

Statuses below follow the bounded round-3 scope.

| Requirement | Status |
|---|---|
| F1: authenticate E at reserved M/K locations | **done** |
| F1: completed rotation requires K when E=N | **done** |
| F1: validate every published document and phase | **wrong** — completed phase S survives |
| F1: unconditional closure exploration with explicit equivalence | **done** — conditional skip removed; equivalence documented |
| F1: three requested negative controls | **done** — all pass by detecting their mutations |
| F2: reject case/trailing-period/trailing-space aliases before mutation | **done** — `dayz_test_storage.py:498` |
| F3: refuse incompatible intact legacy prepared state | **done** — `dayz_test_storage.py:755` |
| F4: journal-project completion, conditional reseal, preserved result | **done** — `dayz_test_storage.py:934` |
| CHANGES 1: independent schema and captured original identity | **done**, retained |
| CHANGES 2: D5 and validation before mutation | **done** for the prescribed alias correction |
| CHANGES 3: authenticated preservation and legacy branches | **done** for the prescribed intact-state correction |
| CHANGES 4: finish A, reseal X, confirm and retain result | **done** |
| CHANGES 5–7: blocked recovery results, exact audit reason, observation identity | **done**, retained |
| CHANGES 8 / mandatory model oracle | **wrong** — F1 remains |
| D3 consumer regression | **done**, retained; not rerun locally |
| Existing storage tests unchanged | **done** — both modules match the supplied base |
| Every existing test passing | **missing** — supplied gate retains two baseline failures |
| Packaged-module lock regenerated and matching | **done** — read-only `--check` passed |
| Round-3 fixes only; `ROUND 3 FIXES` report section | **done** for verified production delta and report |
| No Enforce/PBO changes or prohibited execution | **done** |

## GATE GAP

The gate can remain green when a completed journal records a phase that never constitutes completion. Shared production validation establishes document syntax but misses the filename-dependent phase invariant.

The tests also do not establish real NTFS identity behavior or power-loss durability.

## PREMISE

`REPORT.md:41` reports zero fast-tier failures; the orchestrator’s supplied measurement reports **two, zero new**. `GAUNTLET_GATE: PASS` therefore establishes no additional failures under that gate, not a globally passing suite.

The fresh-transaction equivalence is now explicit. Ignoring completed history still requires independently checking its invariants first.

## NOT VERIFIED

Executed locally on Python 3.14:

- Four in-memory model/oracle tests: **passed**.
- Eleven read-only existing storage tests: **passed**.
- Packaged-module lock check: **passed**.
- F2–F4 probes against current and reconstructed round-2 code.
- The surviving F1 mutation above.

Disk-backed suites were not rerun because this sandbox is read-only; their results are orchestrator-supplied evidence. No files were modified. No DayZ, daemon, MCP tool, installer, pip or git was run. Real NTFS recovery, power-loss behavior, launcher rebuild and in-game acceptance remain unverified.

