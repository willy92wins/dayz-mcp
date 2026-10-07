VERDICT: CHANGES_REQUIRED

## FINDINGS

### F1 — P2 — Fixed-point equivalence still erases collision-producing abort records

`tools/tests/test_storage_rotation_model.py:503`, `:513`, `:537`, `:800`

**Executed scenario:** Starting from `_seed_nodes(_canonical_marker(SEAL_OTHER))`, plant an occupied directory D named `storage_1.modset-d3-legacy`. Create two snapshots with one valid completed `prepared` journal recording that D and its K:

- Snapshot A: journal txid and completed filename use `"1"*32`.
- Snapshot B: journal txid and completed filename use `"9"*32`.
- Both record the original marker’s full hash and `old_seal=SEAL_OTHER`.

Run `_check`, compare `_closure_key`, then invoke `_run` with `seal=SEAL_A`, caller txid `"1"*32` and no faults.

Observed:

```text
oracle checks: both PASS
closure equal: True
C at caller identity False backup_name_collision
C at unrelated identity True seal_changed
```

These snapshots have different continuations but are equivalent to the fixed-point gate. The binding directive explicitly forbids erasing collision-producing files. Preserve their identity and contents; abstract only an eligible consumed retry prefix, with allocation and immutability assertions.

### F2 — P2 — The model destroys the directory-entry spelling required by M1

`tools/tests/test_storage_rotation_model.py:50`, `:246`, `:261`

**Executed scenario:** Plant S2 in `MemFS`: W absent, D containing the original world, M containing `b"original-marker"`, K absent, and a valid schema-2 prepared journal. Create that journal through `fs.open(..., "xb")` under:

```text
STORAGE_1.modset.rotation.11111111111111111111111111111111.json
```

The model enumerates it as:

```text
storage_1.modset.rotation.11111111111111111111111111111111.json
```

Recovery X consequently returns:

```text
launch_allowed=True
reason=recovered_storage_moved
```

The required alias fixture must retain its spelling and return `journal_name_invalid` with an identical snapshot. The production scanner now refuses this alias correctly; the mandatory independent model extension remains missing.

### F3 — P2 — The required existing-test compatibility gate remains red

`tools/dayz_mcp/dayz_test_tool.py:1648`; `tools/tests/test_dayz_test_tool.py:1175`, `:1328`, `:1414`, `:1491`

From `tools/`, using the project venv:

```text
python -B -m unittest tests.test_dayz_test_tool.DayzTestExecutionTest.test_typed_readiness_failure_uses_the_same_envelope
```

Observed:

```text
AssertionError: Items in the first set but not the second:
'storage_recovery_reason'
```

I independently reproduced the same failure in all four envelope tests named in the brief.

The new field is required by E2. Complete the contract migration by updating the existing envelope assertions, including null behavior for success and unrelated failures, and rerun the gate.

### F4 — P3 — Documentation changes invalidate shipped size claims

`PROJECT-MAP.md:84`, `:92`; `README.md:253`; `CHANGELOG.md:31`

Executable scenario:

```text
python -B -m unittest tests.test_docs_truth.ProjectMapSizesDocsTest.test_doc_size_claims_within_rounding
```

Observed:

```text
CHANGELOG.md: claims 48 KB, file is 50497 B (49.3 KiB)
README.md: claims 23 KB, file is 24769 B (24.2 KiB)
```

The CHANGELOG addition is also outside the permitted change list. Resolve the size drift within the authorized scope.

## SPEC COVERAGE

| Requirement | Status |
|---|---|
| Previous F1: import syntax | **Done.** Public tool imports successfully. |
| Previous F2: public diagnostics | **Done.** Reason and canonical remediation survive assembly. |
| Previous F3: model and regressions | **Wrong/incomplete.** F1–F2 above remain; several required extensions are absent. |
| Previous F4: positive short publication reads | **Done.** One-byte-read regression passes. |
| Previous F5: packaged lock | **Done.** `write_packaged_modules_lock.py --check` passes. |
| Previous F6: oversized classification | **Done.** Independent memory check confirms invalid classification, full digest and intact preservation. |
| Previous F7: completed alias exemption | **Done.** Spelling is checked before exemption; alias controls refuse unchanged. |
| Previous F8: shipped recovery guidance | **Done for document/link presence.** Operator regression/model coverage remains incomplete. |
| M1 | **Change done; coverage partial.** Model spelling preservation and alias fixtures missing. |
| M2 | **Change done; required regression/oracle extension missing.** |
| M3 | **Change present; required reseal/retry lifecycle regression and measured replay oracle missing.** |
| D1 | **Change works in independent same-identity checks.** Required regression and same-identity cut exploration missing; equivalence remains wrong. |
| D2 | **Change done; required regression and ordinary-classification negatives missing.** |
| D3 | **Change done; coverage partial.** Missing specified cap spies, tail-corruption controls and cut exploration with short digest reads. |
| E2 | **Transport works.** Independent fake-broker checks passed for all four reasons under healthy/degraded cleanup, with zero fake spawns. Shipped regression covers only compact-result assembly; refusal-witness public oracle missing. |
| E1/E3 | **Procedure and README link present.** Required restoration/quarantine scenarios and external model transitions missing. |
| Original producer/recovery cuts | **Retained; depth-two test passes.** That PASS does not establish the newly required closure properties. |
| Whole fast tier | **Wrong: FAIL.** All five supplied failures reproduced with the project venv. |
| Scope | **Wrong.** Additional CHANGELOG edit. |
| `ROUND 6 FIXES` report | **Present**, but its PASS/completion claims conflict with current evidence. |

## GATE GAP

Beyond F1–F2, I executed another false-pass control: add an original empty directory, rotate successfully, delete that directory from D, then run `_check`. It reports **PASS**. The world oracle compares file contents rather than the complete original tree, so empty-directory loss escapes it.

The gate also lacks actual lifecycle replay measurements, same-caller retry exploration, public diagnostics for each refusal witness, and explicit operator restoration/quarantine transitions.

## PREMISE

The report’s gate PASS is contradicted by the supplied rerun and my reproduction.

The supplied patch uses `base_v11t2` paths and omits the current README addition and `docs/STORAGE_RECOVERY.md`. It is insufficient by itself to establish the complete merge payload. `_audit_context/SPEC_R9B_IMPL.md` is absent here; I used the binding design supplied in the brief.

The claim that storage is identical to main `e14fe85` was not verified through git, which was prohibited.

## NOT VERIFIED

No files were modified; no commit was made. No DayZ, daemon, MCP tool, installer, pip, git or launcher rebuild was run.

I ran targeted read-only/in-memory checks, the depth-two model and the lock check. I did not rerun the entire fast tier or verify real NTFS crashes, power-loss durability, deployed launcher behavior or in-game acceptance.

This batch returns to the orchestrator with blocking findings; no further fix round was started.

