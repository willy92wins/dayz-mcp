VERDICT: CHANGES_REQUIRED

## FINDINGS

### F1 — P1 — The mandatory model gate still accepts violations of its oracle

`tools/tests/test_storage_rotation_model.py:613`, `:636`, `:458`

Termination and distinct X/Y recovery are now checked, but preservation and closure remain incomplete.

**Executed failure scenarios:**

- Produce a completed rotation with original E present. Delete its reserved K and put E in `unrelated.marker.json`. Calling `_check(snapshot, original_world, E, current_seal)` **passes** because any filename ending in `.marker.json` counts as K.
- With E=N, delete K and leave E only in canonical M. The same oracle **passes**, although a completed rotation requires preservation in K.
- Monkeypatch `_complete_journal` to call the original, then open C with `"wb"`, write `b"{"`, flush and fsync. Run `test_depth_two_fixed_point_preserves_world_and_marker` in that process. **Wrong output: `OK`**, despite 37,797 malformed completions.

The closure check also contains:

```python
if _shape(recovered) == _shape(state):
```

This skips third-pass cuts whenever clean recovery changes the state. Instrumenting the stock test found **202 skipped states out of 232**, covering 15,340 operations. Running the absent-marker fixture with that condition replaced in memory by `if True:` fails the closure assertion: a prepared active journal with txid `"4"*32` produces an authoritative shape absent from the recorded set.

**Fix:** authenticate E at the exact reserved M/K locations according to phase, validate published documents after every cut, and explore every continuation unconditionally. Define the equivalence used for closure explicitly rather than skipping transitions.

### F2 — P1 — Windows aliases still mutate artifacts before refusal

`tools/dayz_mcp/dayz_test_storage.py:491`

The fix compares:

```python
{name.casefold() for name in reserved}
```

Case folding does not cover trailing-period or trailing-space aliases. These names also pass `_plain_artifact_name`.

**Executed input:**

- Schema-2 J, `phase="storage_moved"`, txid `"1"*32`.
- W absent; D=`storage_1.modset-alias` contains O.
- M=`b"old-invalid"`.
- `old_marker_state="present_invalid"`, with its correct SHA256.
- `marker_backup="storage_1."`; old seal null; new seal `"a"*64`.

On the filesystem model extended with Windows trailing-period normalization, recovery **accepts the journal, renames M onto W, then returns `journal_state_impossible`**. Output: changed snapshot, one rename, W now a file containing `b"old-invalid"`.

Read-only host checks confirmed that `tools`/`tools.`, `REPORT.md`/`REPORT.md.`, and `REPORT.md`/`REPORT.md ` each satisfy `os.path.samefile`. Microsoft also documents the trailing-period/space naming restriction. [Windows naming rules](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)

**Fix:** reject filesystem-equivalent authoritative names before mutation, including these Win32 aliases.

### F3 — P1 — The corrected legacy branch still authorizes an incompatible intact world

`tools/dayz_mcp/dayz_test_storage.py:754`, `:758`, `:762`

The new branch accepts a preserved old marker while canonical M carries the new seal:

```python
elif not preserved or not (m_kind == "absent" or is_n):
    return "refuse"
```

**Executed input:**

- Schema-1 J in `prepared`.
- W contains O; D absent.
- `old_seal="c"*64`, `new_seal="a"*64`.
- K contains a valid marker for `"c"*64`.
- M contains canonical N for `"a"*64`.
- Call recovery for `"a"*64`.

**Wrong output:** `launch_allowed=True`, `storage_rotated=False`, `decision="reuse"`, `reason="seal_matches"`. J is completed while W remains untouched.

This is outside the observable legacy graph: preservation/publication follows moving W. K authenticates the old marker; it does not establish that the untouched world is compatible with the new marker.

**Fix:** refuse this intact prepared combination before completing J. Limit the preserved-K exceptions to the moved-world legacy states.

### F4 — P2 — Same-seal recovery rejects a changed project label after completing the transaction

`tools/dayz_mcp/dayz_test_storage.py:951`

When the caller’s seal equals the journal’s seal, recovery compares N against canonical bytes built with the caller’s project:

```python
expected_x = _canonical_marker(call_seal, project)
```

**Executed input:**

- Valid schema-2 S; D=O; original marker absent; M/K absent.
- Journal seal `"a"*64`, project `"DayZ_MCP"`.
- Call with the same seal and `project="OtherProject"`.

**Wrong output:** `recovery_marker_mismatch`, after J→C and successful publication of N. No active journal remains. Repeating the call then succeeds as `seal_only`, reporting `storage_rotated=False`.

The design requires finishing N with the journal’s project, resealing when A≠X, and confirming seal X. A different project label alone does not justify rejecting an otherwise valid same-seal recovery.

**Fix:** make same-seal confirmation follow that contract and preserve the successful rotation result.

## SPEC COVERAGE

| Requirement | Status |
|---|---|
| CHANGES 1: independent journal version, captured state/hash, schema bifurcation | **done** |
| CHANGES 2: D5, validation before mutation, pure selector | **wrong** — incomplete Windows alias rejection |
| CHANGES 3: authenticated preservation and legacy compatibility | **wrong** — F3 |
| CHANGES 4: finish A, reseal X, confirm, retain rotation result | **wrong** — F4 |
| CHANGES 5: blocked results for returnable recovery failures | **done** — zero-write and final-K-stat probes now return `recovery_finish_failed`, preserving completed publications |
| CHANGES 6: exact refusal reason in lifecycle audit | **done** — executed probe retained `journal_state_impossible` under public `storage_recovery_required` |
| CHANGES 7: observation identity copied and retained on load | **done** — source verified; loader probe passed |
| CHANGES 8: required enumerator and independent regressions | **wrong** — F1; D3 regression is present |
| Reject active Q with W and D present | **done** |
| Schema-1 acceptance without invented historical hashes | **wrong** — compatibility is implemented, but widened beyond the admitted intact state |
| Every-operation crash enumeration, X/Y recoveries, depth-two closure | **wrong** — recovery cuts are conditionally omitted; short-return exploration covers producer writes only |
| Oracle: complete O under W or reserved D | **wrong** — oracle ignores directory entries and accepts any matching backup |
| Oracle: E at pending M or preserved reserved K | **wrong** — F1 |
| Oracle: complete published documents/phases | **wrong** — malformed C mutation passes |
| Oracle: successful termination after generated cuts | **done** for enumerated states; **missing** for omitted continuations |
| Oracle: launch authorization constraints | **wrong** — world-present classification is not independently checked |
| Oracle: idempotence | **missing** — asserted only for selected finished Y states |
| Oracle: every negative predicate, identical snapshot, zero spawn | **wrong** — selected cases only; snapshots omit directories; only rename/replace are counted |
| Oracle: D3 consumer scenario | **done** in source; execution unavailable here |
| Existing tests unchanged | **done** — both existing storage test modules match the supplied base |
| Every existing test passing | **missing** — supplied gate reports two inherited failures; disk-backed suites unavailable locally |
| Packaged-module lock regenerated and matching | **done** — `write_packaged_modules_lock.py --check` passed |
| No changes outside CHANGES | **wrong** in supplied diff — also adds empty `tools/approved-launchers.lock`; attribution is unresolved |
| No Enforce/PBO changes or forbidden runtime/tool execution | **done** |

The original F3, F4, F5 and F6 scenarios now return the expected blocked results. F7 is fixed: CHANGELOG matches the base and the size test passes. F1 and F2 are only partially fixed.

## GATE GAP

A PASS still permits malformed completed journals, missing reserved marker preservation, omitted recovery transitions, Windows aliases, and unsafe legacy intact-world acceptance. The witness checks require neither every specified row nor every edge; S3a/S4/S5 are collapsed into S3.

The gate therefore establishes regression results for its sampled cases, not the binding recovery oracle.

## PREMISE

- `REPORT.md` claims zero fast-tier failures; the orchestrator’s current measurement reports **two**, with zero new failures.
- Exact snapshot closure needs an explicit equivalence for changing transaction identities and completed history. Fresh txids can create new concrete journal names; the current conditional check does not resolve that premise.
- The empty launcher-lock addition cannot be attributed from the supplied material. I did not run git.
- A globally green existing suite is not established by a gate that permits inherited failures.

## NOT VERIFIED

Executed on Python 3.14: the stock in-memory model, counterexamples above, documentation-size test, lock check, lifecycle audit probe, and observation loader probe.

Disk-backed suites were attempted but could not be validated: the read-only sandbox prevents temporary-file creation (`No usable temporary directory`). Their orchestrator results remain supplied evidence.

No DayZ, daemon, MCP tool, installer, pip or git was run. No files were modified. Real transaction recovery on NTFS, power-loss durability, launcher rebuild, and in-game acceptance remain unverified.

