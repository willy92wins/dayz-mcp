# r9c: the r9b DZ-R9 step-6 findings (storage rotation recovery), tests and oracle for production changes already in the tree

## ITEM r9c

### Context (binding)
The storage rotation recovery of r9b (round 4, merged as #221) is on main. Its DZ-R9 step-6 re-audit found M1-M3, D1-D3,
E2 and E1/E3 (`r9_r9b/R9-TABLE.md`). gpt-6.1-sol wrote the fix specification below. A previous attempt (r9b round 6,
Grok) implemented ALL EIGHT production changes and the reviewer confirmed each works in independent checks; those changes
are already in this workspace. What is missing is the required regression tests, the model-oracle extensions and the
migration of the existing envelope tests to the new E2 field: that is this batch. Keep the production changes unless a
test proves one wrong, and then fix it minimally.

### Fix specification (gpt-6.1-sol, binding)
## IMPLEMENTATION SPEC

**One fix round, limited to M1–M3, D1–D3, E2, and E1/E3.** The approved recovery graph in `_audit_context/SPEC_R9B_IMPL.md` remains binding. All changes below are **[DESIGN]**, not applied changes.

Notation: **W** = `storage_1`; **D** = reserved world backup; **M** = marker; **K** = reserved original-marker backup; **J/C** = active/completed journal; **O/E** = original world/marker bytes; **A** = journal’s `new_seal`; **X** = requested seal.

Source abbreviations:

- **S**: `tools/dayz_mcp/dayz_test_storage.py`
- **L**: `tools/dayz_mcp/process_lifecycle.py`
- **Model**: `tools/tests/test_storage_rotation_model.py`

Keep journal schemas 1/2, marker format, original transaction fields, backup naming, legacy recovery decisions, and existing admission/lifecycle rules. No Enforce, PBO, installer, daemon, or deployment changes.

### M1 — Journal-family aliases must block before classification — P1

**Change — DATA-CRITICAL.** In `S._active_journals` (`S:995–1018`), detect membership of the journal family using Win32 segment identity: case folding and removal of trailing spaces/periods, matching the existing rule in `S._win32_file_identity` (`S:259–261`).

Detection must be broader than acceptance:

- Canonical active names continue through the existing grammar.
- A journal-family entry whose spelling differs from its Win32 identity is malformed, producing `journal_name_invalid`.
- Never normalize an alias and then recover through a reconstructed canonical pathname.
- Preserve the existing exemption for canonically spelled names ending in `.completed.json`, including `…garbage.completed.json`. Tightening that exemption is outside this round.

**Regression.** Plant schema-2 S2: W absent, D holding O, M holding E, K absent, valid J under an upper-cased family name. Call `prepare_storage(..., seal=X)`.

Expected: `launch_allowed=False`, reason `journal_name_invalid`, identical complete snapshot, E unchanged, zero spawn. Parameterize mixed case and trailing-space/period aliases; include canonical-name recovery as the positive control.

**Red on this tree:** the in-memory enumeration-alias scenario returned `storage_absent` and replaced E; E was no longer retained.

**Oracle extension.** Preserve directory-entry spelling separately from Win32 lookup identity. Add alias fixtures to refusal coverage; the oracle must discover them independently of the production scanner. This requires correcting the model’s case-sensitive journal discovery where necessary.

### M2 — A non-directory W is not absence — P2

**Change — DATA-CRITICAL.** In ordinary classification in `S.prepare_storage` (`S:1152–1159`), inspect W’s kind explicitly before calling `should_rotate`:

- `absent` → existing absent-world classification.
- `dir` → existing present-world classification.
- `file`, `link`, or `other` → blocked result, reason `storage_not_a_directory`.

Observation errors remain `recovery_observation_failed`. Do not change the pure Boolean decision matrix in `should_rotate`.

**Regression.** Plant a regular file W containing known bytes, M absent, no J. Call `prepare_storage`.

Expected: refusal with `storage_not_a_directory`; file, directories, and marker absence unchanged; zero spawn. Add valid matching/mismatching marker variants so marker compatibility cannot authorize the unsupported W. Cover link/other kinds in memory.

**Red on this tree:** the file scenario returned `launch_allowed=True`, `storage_absent`, and added M.

**Oracle extension.** Track artifact kinds, including empty directories. A successful ordinary classification requires W to be absent or a directory. Unsupported-kind refusals require a complete identical snapshot.

### M3 — Replay must retain reset visibility after A is resealed to X — P2

**Change.** Update `L._pending_completed_rotation` (`L:139–195`) and its caller (`L:3303–3321`).

Use existing completed records; **do not add fields or rewrite `new_seal=A`**:

1. Replay applies only while W is actually absent and the current valid marker carries requested seal X.
2. Consider validated, canonical completed journals in `marker_published` with their reserved D present as a directory.
3. Do not discard a candidate solely because its `new_seal` differs from X.
4. Exactly one candidate → replay that record’s backup/reset observation with `storage_rotated=True`.
5. Multiple candidates → `_AMBIGUOUS_PENDING_ROTATION`; retain the existing unknown observation. Do not pick by txid, filename order, timestamp, or seal preference.
6. Prepared completions are aborts and never count as resets.

The existing caller remains responsible for avoiding a second rotation audit row.

**Regression.** Plant a recoverable moved transaction for A with original E. Recover using X, confirming D=O, K=E, M=X, and C still recording A. Retry through the fake lifecycle launcher before any replacement W is created.

Expected: the retried run records `storage_rotated=True`, the same D, and the reset notice; no second world move or rotation audit row.

**Red on this tree:** recovery returned a rotation, but `_pending_completed_rotation(mission, X)` returned `None`.

Controls: W recreated → no pending replay; prepared C → no reset; missing D → no candidate; two qualifying published records → unknown.

**Oracle extension.** After recovery and at every subsequent lifecycle-replay boundary, read the **actual completed record**, including its phase, A, project, and reserved backup names. Assert the measured observation separately from marker correctness. A successful reseal followed by a retry cannot become measured `False` when a unique completed reset remains pending.

### D1 — Retained abort journals must not exhaust one launch identity — P2

**Change — DATA-CRITICAL.** Replace the single derived-ID choice in `S.prepare_storage` (`S:1138–1151`) with deterministic allocation before a newly required rotation.

Binding rule:

- `t₀ = caller txid`
- `tₙ₊₁ = derived_txid(tₙ)`
- Keep the existing derivation: SHA-256 of ASCII `"<txid>:retry"`, first 32 lowercase hex characters (`S:1095–1099`).

Start from t₀ on **every invocation**, including invocations without an active journal. Advance past a candidate only when its canonical C is a validated completed `prepared` abort and its recorded D/K are absent. Preserve that C unchanged.

An active J must be reconciled first. Arbitrary files, unreadable/invalid C, published C, unsupported types, or occupied backup reservations retain collision/refusal behavior. Never overwrite, delete, or repurpose retained journals. Observation failures block.

Traversal must terminate for a finite snapshot; detect a repeated candidate rather than looping. No randomness, clock-derived transaction IDs, or new configurable retry budget.

**Regression.** Use the same caller txid, X, and time throughout:

1. Plant intact prepared t₀ with W=O, M=E, D/K absent.
2. Recovery aborts t₀ and creates prepared t₁.
3. Abruptly abandon execution immediately before W→D.
4. Retry: abort t₁, allocate t₂, and finish without faults.
5. Repeat the healthy call; it must not create another rotation.

Assert both abort records remain byte-identical, O/E are preserved, the successful transaction uses independently calculated t₂, and no active J remains. Include a second consecutive pre-move death to exercise t₃ and the no-active-journal path with retained aborts.

**Red on this tree:** after the first death, three identical retries returned `backup_name_collision`.

**Oracle extension.** Add recovery paths that reuse the **same caller identity**, rather than exclusively using fresh `"2"*32`, `"3"*32`, etc. Record the occupied abort prefix and independently calculate the next transaction ID. Inject cuts around candidate observation, abort completion, journal publication, and W movement.

### D2 — Unsupported marker types need a precise seal-only refusal — P3

**Change.** In ordinary `S.prepare_storage`, validate M’s kind before reuse/seal-only publication, matching `rotate_storage`’s existing check (`S:1039–1041`).

Only absent/file markers enter ordinary classification. Directory/link/other markers return `marker_type_unsupported` before any publication or temporary creation. Observation errors remain distinct.

**Regression.** Plant W absent and a directory M, including a child file. Call `prepare_storage`; also exercise the fake lifecycle caller.

Expected: blocked result with `marker_type_unsupported`, unchanged complete snapshot, zero spawn, precise public reason through E2. Parameterize present W and in-memory link/other markers.

**Red on this tree:** the absent-W scenario raised `FileExistsError`; the lifecycle wrapper maps it to `storage_rotate_failed`.

**Oracle extension.** Add unsupported M kinds to ordinary-classification negatives, including the absent-W branch.

### D3 — Bound JSON reads and contain parser recursion — P3

**Change — DATA-CRITICAL.** Update `S._parse_json`, JSON-reading paths, original-marker capture, and recovery observations.

- Catch `RecursionError` alongside the existing decoding/parsing failures (`S:285–291`).
- JSON input remains capped at 65,536 bytes. Read at most 65,537 bytes, accumulating positive short reads until EOF or that limit. Do not perform an unbounded read before applying the cap.
- Apply bounded reads to active/completed journal loading, marker classification, and publication comparisons.
- **Do not hash a truncated original marker.** Capture original-marker classification bytes up to the limit while streaming SHA-256 over the entire file in bounded chunks. Use the full digest for schema-2 capture and M/K authentication.
- Thread observed full digests through the existing schema-2 preservation checks where required. This changes observation plumbing, not the state graph.
- Oversized original markers remain invalid originals that rotation preserves intact through M→K. Do not impose a new size-based refusal on otherwise recoverable original bytes.
- Keep all reads instrumentable by the model.

**Regressions.**

1. Canonical active J containing `b"["*30000 + b"0" + b"]"*30000`: return `journal_unreadable`, no exception, identical snapshot.
2. The same 60,001-byte payload as M over a present W: classify invalid, rotate successfully, preserve all E at K.
3. Oversized journal: refuse without reading beyond cap+1.
4. Oversized M: successful rotation/recovery authenticates and preserves every byte. Corrupting only bytes beyond the JSON cap must fail original authentication.
5. Read spies reject `read()`/negative sizes; positive short-read variants must produce the same decisions.

**Red on this tree:** the nested payload raised `RecursionError`; the JSON read spy observed `read(-1)`.

**Oracle extension.** Inject cuts and short returns in bounded reads and streaming digest reads. Independently hash complete E/K; add equal-prefix/different-tail controls so truncated hashing cannot pass.

### E2 — Carry precise refusal and guidance through the public result — P3

**Change.** Preserve the top-level error code `storage_recovery_required`. Add narrowly validated diagnostic fields through:

- `L._rotate_storage_for_launch` and start settlement (`L:3322–3341`, `L:4250–4266`);
- `dayz_test_worker.DayzTestWorkerError`, `_failed`, `_failure_after_cleanup`, and `_start`;
- native launcher `app_main.py` terminal serialization and exception handling;
- `dayz_test_tool.WorkerTerminal`, terminal validation, and public-result assembly.

Binding fields:

- `storage_recovery_reason`: precise declared storage refusal token.
- `storage_recovery_hint`: canonical guidance for that token, carried in the failure terminal.
- Public result: expose `storage_recovery_reason` and place the guidance in existing `remediation`.

Use a shared closed vocabulary covering every refusal produced by S. Generate canonical hints from that vocabulary; reject inconsistent or malformed terminal diagnostics. Do not forward arbitrary daemon text.

Diagnostics remain optional in older terminals. Success and unrelated failures carry no storage diagnostic pair. Preserve attempt identity, cleanup degradation, audit-failure reporting, and existing error codes. Public reason is null when unavailable; do not infer it from unrelated status rows or audit history.

**Regression.** For `journal_state_impossible`, `journal_unreadable`, `journal_name_invalid`, and `journal_ambiguous`, run a temporary/in-memory refused lifecycle start through the fake broker, worker, serialized terminal, parser, and public result.

Expected: exact reason and actionable remediation survive; top-level code remains unchanged; zero spawn. Cover healthy/degraded cleanup and legacy/malformed terminal controls.

**Red on this tree:** a fake lifecycle response containing precise reason and hint produced a worker exception retaining neither; its serialized terminal omitted both.

**Oracle extension.** For each refusal witness, verify the public diagnostic pair alongside the identical mission snapshot and zero-spawn invariant. Mission immutability applies to the refusal operation; later operator restoration is a separate action.

### E1/E3 — Ship an operator recovery procedure — P3

**Change — DATA-CRITICAL guidance.** Add `docs/STORAGE_RECOVERY.md`, link it from README’s launch documentation, and reference it in E2 guidance.

The shipped procedure must state:

- Stop retries while inspecting; obtain exclusive mission access and confirm managed DayZ processes are stopped.
- Preserve a verified copy/inventory of all artifacts before manual repair.
- Identify the journal’s schema, phase, D/K names, and schema-2 original-marker hash.
- For `storage_moved`/`marker_published` refusals caused by a renamed/missing backup, restore the **original complete tree** to recorded D and original marker bytes to recorded K when required. Preserve W absence for those moved states.
- A second copy under the exact D name while the original remains elsewhere is different from having simultaneous W and D; W+D remains refused in the moved/final states.
- If original content cannot be authenticated/restored, stop and escalate. Do not fabricate a seal or delete the transaction evidence.
- For malformed family entries such as `.json.bak` copies or aliases, inventory them and quarantine only positively identified stray copies **outside the reserved journal family**, preserving their bytes. Do not rename an uncertain file into a canonical active journal.
- Explain the existing `.json.bak` blocking behavior and canonically spelled `…garbage.completed.json` exemption.
- Retry after restoration; retain D/K and completed journals. Rotation/reset visibility does not prove the game rebuilt the world.

**Regression.** Add a shipped-document test that checks README discoverability, the recovery link used by guidance, and required phase-specific procedures/examples.

Pair it with operator scenarios:

- Remove/rename D after `storage_moved` and after `marker_published`: refusal leaves the snapshot unchanged.
- Restore exact D/K layout: recovery succeeds and preserves O/E.
- Add a `.json.bak` stray: refusal.
- Quarantine that identified stray: recovery succeeds.
- `…garbage.completed.json`: retain existing exemption.

**Red on this tree:** the shipped recovery document/link is absent. The restoration controls already pass and must remain green; they are not falsely presented as new behavioral failures.

**Oracle extension.** Model operator restoration/quarantine as explicit external transitions between refusal and recovery. Product code must not perform those repairs automatically.

### Shared model and acceptance gate

Extend the existing Model; retain its original cut coverage and invariants.

The current `_closure_key` discards every completed journal (`Model:425–461`). That is insufficient for M3/D1. Binding corrections:

- Exact snapshot deduplication retains completed names and bytes.
- Fixed-point equivalence retains published-record information consumed by replay and whether selection is unique or ambiguous.
- Abort-history abstraction may collapse only a **validated eligible consumed retry prefix**, with allocation correctness and retained-record immutability asserted separately. It cannot erase collision-producing files.
- Keep consumer-distinct states distinct: current seal, completed-record A, backup identity, candidate multiplicity, and next usable retry position matter.
- Preserve producer × every cut → recovery X × every cut → recovery Y × every cut coverage, and add same-identity retries.
- Include abrupt abandonment before/after every newly introduced I/O operation; never substitute normal exception cleanup for death.

**Acceptance:** each listed red regression passes after the patch; existing affected tests pass; the extended model gate passes with witnesses for the new scenarios. A failed criterion returns the round to the orchestrator—no automatic additional fix round.

**Packaged lock: yes, regeneration is required.** S, the worker, and launcher `app_main.py` are pinned. After implementation run:

```text
python tools/write_packaged_modules_lock.py
python tools/write_packaged_modules_lock.py --check
```

This updates source-lock evidence only. Rebuilding or replacing a deployed launcher is outside this read-only specification.

### Where the previous attempt stopped (gpt-6.1-sol, round-6 review of the same changes)
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
