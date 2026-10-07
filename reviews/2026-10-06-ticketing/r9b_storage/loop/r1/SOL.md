VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P1 — The mandatory model gate passes recovery that never completes.**  
`tools/tests/test_storage_rotation_model.py:452`, `:456`, `:352`

The test only rejects `journal_state_impossible`:

```python
if result is not None:
    self.assertNotEqual(result.reason, "journal_state_impossible")
```

**Executed failure scenario:** wrap `prepare_storage` so that every call with an active journal returns `_blocked("recovery_finish_failed", seal)` without recovering anything; otherwise call the original implementation. Then run `test_depth_two_fixed_point_preserves_world_and_marker`. **Wrong output: `OK`**, despite 184 blocked recovery calls.

Other independently reproduced gaps:

- Both recovery depths use X; recovery Y is missing.
- Extending the loop to depth 3 discovers **14 additional authoritative states** for the absent-marker fixture. Depth 2 was not a fixed point.
- `_marker_kept` accepts E anywhere: with M/K absent and E present only in T, it returns `True`.
- An “after open(xb)” snapshot omits the newly created file; an “after write(b"abc")” snapshot retains empty bytes. `finish_after()` runs before those mutations.
- Cuts freeze a snapshot while execution continues; they do not abandon execution.
- The enumerator swallows exceptions, omits final launch/termination assertions, discards temporal coverage during deduplication, and records no row/edge witnesses.

**Fix:** implement the specified enumerator and independent, location-sensitive oracle. Require successful termination of every reachable state, distinct X/Y recovery, and mechanically demonstrated closure.

**F2 — P1 — Windows case aliases mutate artifacts before refusal.**  
`tools/dayz_mcp/dayz_test_storage.py:478`, `:486`

Alias validation compares literal strings:

```python
if len(reserved) != 6:
    return None
```

**Executed failure scenario:** schema-2 J in `storage_moved`; W absent; D contains O; M contains `b"old-invalid"`; original state/hash correctly recorded; K’s journal name is `"STORAGE_1"`. On the case-insensitive filesystem model, the validator accepts K although it aliases W.

Recovery renames M to W, then returns `journal_state_impossible`. **Wrong output: refusal with a changed snapshot and one rename.** Subsequent recovery remains blocked because W is now a file.

**Fix:** reject filesystem-equivalent artifact names before mutation.

**F3 — P1 — A transient observation error resets an already compatible world.**  
`tools/dayz_mcp/dayz_test_storage.py:289`, `:1149`

```python
except OSError:
    return None
```

**Executed failure scenario:** W contains O; M is a valid canonical marker for X; no active journal. Make only the first read of M raise `PermissionError`; subsequent reads succeed. Call `prepare_storage` for X.

**Wrong output:** `launch_allowed=True`, `storage_rotated=True`, with O moved to a backup. The failed observation became an invalid-marker classification, and the later successful capture did not reconsider that decision.

**Fix:** distinguish observation failure from invalid bytes and block before creating a transaction. This is an unmet binding requirement, although the swallowing behavior also existed in the base.

**F4 — P1 — Legacy prepared recovery accepts a known-original mismatch and authorizes reuse.**  
`tools/dayz_mcp/dayz_test_storage.py:754`

```python
and not _legacy_seal_bytes(m_bytes, old)
and not is_n
```

**Executed failure scenario:** schema-1 J is `prepared`; W contains O; D/K absent; `old_seal="c"*64`; `new_seal="a"*64`; M contains canonical N for `"a"*64`. Call recovery for `"a"*64`.

**Wrong output:** `launch_allowed=True`, `storage_rotated=False`, `decision="reuse"`, `reason="seal_matches"`. J is completed despite the missing known original, and W is trusted under the new marker.

This violates refusal predicate 10; the permitted legacy exceptions do not authorize this intact, known-original mismatch.

**Fix:** authenticate the pending known original before aborting/classifying the intact world.

**F5 — P2 — Returnable recovery errors escape the declared result contract.**  
`tools/dayz_mcp/dayz_test_storage.py:956`, `:965`

```python
except OSError:
    return _blocked("recovery_finish_failed", call_seal)
```

**Executed failure scenarios:**

- Valid schema-2 S, D=O, original absent, M/K absent; make marker writes return zero. **Wrong output:** raised `StorageError("short_write")`, instead of a blocked recovery result.
- Recover the same valid state normally, but make the final K stat fail after J→C. **Wrong output:** raised `PermissionError`; that stat occurs outside the recovery handler.

At lifecycle, these exceptions become `storage_rotate_failed`, bypassing the exact recovery-reason audit.

**Fix:** handle publication `StorageError` appropriately and include final result observations inside the recovery error boundary, preserving completed publications.

**F6 — P2 — A malformed journal raises instead of returning `journal_unreadable`.**  
`tools/dayz_mcp/dayz_test_storage.py:460`

```python
or document.get("phase") not in _PHASES
```

**Executed failure scenario:** otherwise correctly keyed schema-2 journal with `"phase":[]`.

**Wrong output:** `TypeError: cannot use 'list' as a set element`, rather than `journal_unreadable`. The snapshot remains unchanged, but the declared refusal contract is broken.

**Fix:** validate field types before set membership.

**F7 — P2 — The out-of-scope changelog addition introduces a confirmed existing-test failure.**  
`CHANGELOG.md:31`; `PROJECT-MAP.md:84`

The patch adds 339 bytes to CHANGELOG while the map still states:

```text
- `CHANGELOG.md` - 48 KB, touched 2026-10-06 (UTC)
```

**Executed failure scenario:**

```text
python -B -m unittest tests.test_docs_truth.ProjectMapSizesDocsTest.test_doc_size_claims_within_rounding
```

**Wrong output:** FAIL: file is 50,461 B / 49.3 KiB. The base file is 50,122 B and remains within the test’s tolerance.

**Fix:** remove the changelog addition, which is outside CHANGES, or obtain an explicit scope amendment for the documentation update.

## SPEC COVERAGE

| Requirement | Status |
|---|---|
| Independent journal version; single original capture; schema-1 compatibility | **done** structurally |
| D5, artifact validation, pure selector | **wrong** — case aliases and malformed field types; F2/F6 |
| Accredited preservation; P→S; legacy exceptions | **wrong** — intact known-original mismatch; F4 |
| Finish A, then reseal X and retain rotation result | **done** on inspected normal paths |
| Blocked results for returnable recovery errors | **wrong** — F5 |
| Lifecycle audit preserves exact rejection reason | **done** for returned refusals; escaped failures bypass it |
| D4 observation identity copied and preserved on load | **done** in source |
| In-memory fault injection at every specified operation | **wrong** — boundary placement and abandoned-execution semantics |
| Producer → recovery X → recovery Y → termination | **wrong** — X is repeated; termination not required |
| Deduplication to a fixed point after depth 2 | **wrong** — additional depth-3 states reproduced |
| Every oracle invariant after every cut | **wrong** — location, launch readiness, complete documents, and termination are inadequately asserted |
| Temporary coverage and row/edge witnesses | **missing** |
| Every refusal predicate; identical complete snapshot; zero spawn | **wrong** — sparse cases, file-only snapshot, and rename/replace counters rather than spawn instrumentation |
| D3 consumer scenario | **done** in test source; filesystem-backed execution unavailable here |
| Existing tests remain passing | **wrong** — F7 |
| Sealed-module lock regenerated | **done** — `write_packaged_modules_lock.py --check` passed |
| Changes restricted to CHANGES; no Enforce/PBO changes | **wrong** — changelog addition outside scope; no Enforce/PBO diff |

## GATE GAP

The current gate can pass a recovery implementation that preserves some bytes but never finishes. It also misses preservation exclusively in a temporary, case aliases, transient read errors, malformed field types, and returnable failures. Passing this model test does not establish the specified safety contract.

## PREMISE

The binding design is sufficient to reject this implementation; no design change is needed to address these findings.

The supplied `git check-ignore` failure is listed in `baseline_failures.txt`, so it is not evidence of a new batch regression. The documentation-size failure is new and independently reproduced. `REPORT.md:34` claims PASS, which does not describe the supplied current gate result.

## NOT VERIFIED

- Filesystem-backed storage, D3, and lifecycle suites: attempted, but the read-only sandbox provided no usable temporary directory.
- Full fast tier and project-venv execution.
- Real NTFS, power loss, directory durability, deployed launcher, or in-game behavior.

The in-memory model test passed; all failure scenarios above were reproduced without writing files. No files were modified, and no git, DayZ, daemon, or MCP operations were run.

