# AUDIT — r9b storage rotation recovery (lane E: recovery paths, refusals, caller and operator)

Every refusal `prepare_storage` can return, what reaches the `dayz_test_run` caller, and what the operator can
do next. All executable claims were run against this tree with the project venv in temp dirs. Gates re-run here:
`tests.test_storage_rotation_model` 13/13 OK (35.6 s, includes the D3 and observation-log regressions),
`tests.test_dayz_test_storage` 32/32 OK, `packaged-modules.lock.json` 21/21 hashes match the tree.

## FINDINGS

### E1 — P2 — A hand-restore of the world while the journal is past `prepared` refuses every later launch, and no way out is named

Literal, tools/dayz_mcp/dayz_test_storage.py:651-657 and :919-920:

```python
if w == "dir" and d == "dir":
    return "refuse"
if w == "absent" and d == "absent":
    return "refuse"
phase = str(document["phase"])
if phase in {PHASE_STORAGE_MOVED, PHASE_MARKER_PUBLISHED} and d != "dir":
    return "refuse"
...
if action == "refuse":
    return _blocked("journal_state_impossible", call_seal)
```

The journal stays active until a recovery completes it, and every `prepare_storage` re-enters
`_reconcile_journal` first (:1139-1147), so a `refuse` verdict is recomputed identically on every later launch:
the mission never starts again until a human edits disk state. The crash matrix itself is closed (executed: cuts
prepared/storage_moved/K/N/Q all recover; the model gate agrees), but the operator's file verbs on the backup
folder produce states the matrix refuses: rename D→W while the phase is `storage_moved`/`marker_published`
(w dir, d absent → :656), copy instead of rename (w dir d dir → :651), delete D (w absent d absent → :653).
Executed with the real functions: after a mid-`storage_moved` crash, restore-by-rename →
`journal_state_impossible` on launch A, again on launch B, again on retry — three refusals, zero mutations, the
world intact in W. The same repair during `prepared` is absorbed silently (abort, :705-706, then a fresh
rotation), so the operator cannot predict which phase turns their repair into a livelock. The unblock that was
safe in every executed state — delete the active `storage_1.modset.rotation.<txid>.json` once W/D are
hand-reconciled; both copies exist, v1 renames only, and the next call then rotates or publishes cleanly —
appears nowhere; the only operator text (tools/dayz_mcp/process_lifecycle.py:1241-1247) says "Inspect the
storage_1.modset.rotation.* files ... before retrying", and retrying alone never clears it.

Executable (temp dir; `st` = dayz_mcp.dayz_test_storage; A/B 64-hex seals; helpers create `storage_1` and its
marker):

```python
h = sha256(marker_bytes)                      # as the producer would record
doc = st._journal_document(txid=T, phase="storage_moved", new_seal=A, old_seal=B, project="p",
    storage_backup=BACKUP, marker_backup=BACKUP + ".marker.json",
    old_marker_state="present_valid", old_marker_sha256=h)
write_journal(doc); os.rename(root+"/storage_1", root+"/"+BACKUP)   # crash froze here
os.rename(root+"/"+BACKUP, root+"/storage_1")                       # operator restores by hand
r1 = st.prepare_storage(root, seal=A, ...)    # launch=False reason=journal_state_impossible
r2 = st.prepare_storage(root, seal=B, ...)    # idem, every future launch, both seals
os.unlink(root+"/...rotation.<T>.json"); st.prepare_storage(...)    # -> launch=True, no loss
```

### E2 — P3 — The caller is never told which refusal fired, and the shipped hint mispromises

Literal, tools/dayz_mcp/process_lifecycle.py:3329-3341 and :4263-4265:

```python
reason = result.reason if isinstance(result.reason, str) else ""
...
written = self._audit("lifecycle_storage_recovery_required", None, reason, "rejected", run_id=run_id)
...
return "storage_recovery_required"
...
hint = _STORAGE_ROTATE_HINTS.get(storage_error)
if hint is not None:
    settled["hint"] = hint
```

The exact refusal (`journal_state_impossible`, `journal_unreadable`, `journal_name_invalid`,
`journal_ambiguous`, `recovery_*`) is demoted to the daemon audit row; `dayz_test_run` returns the generic code
plus a static hint (:1232-1248). The `storage_rotate_failed` hint promises "an active journal beside storage_1,
which the next call reconciles" — true for pure crashes (executed), false for every E1/E3 state, where the next
call refuses identically. The `storage_recovery_required` hint's only instruction, "Inspect ... before
retrying", is an observation verb, not a remedy — and the copy that inspection invites is itself a refusal
producer (E3). Executable: run the E1 script; `r1.reason` is `journal_state_impossible` on disk, while the only
value the launch path can return is the constant at :3341.

### E3 — P3 — Any stray `storage_1.modset.rotation.*` name, second active journal, or damaged journal blocks launches permanently, same unnamed remedy

Literal, tools/dayz_mcp/dayz_test_storage.py:1014-1015 and :1134-1135:

```python
if _JOURNAL_ACTIVE.fullmatch(name) is None:
    malformed = True
...
if malformed:
    return _blocked("journal_name_invalid", seal)
```

The scanner counts every prefix-sibling that is not grammar-perfect as `malformed`; the module's own temporaries
(`storage_1.modset.tmp-*`) escape only because their prefix differs (executed: a leftover temp does not block).
The natural act during the E2 "inspect" step — `copy ...rotation.<t>.json ...rotation.<t>.json.bak` — lands in
the scanned family with a non-grammar name and blocks every launch (`journal_name_invalid`, executed twice,
cleared by removing the copy). The same permanent loop for two active journals (`journal_ambiguous`, executed
twice) and a truncated journal (`journal_unreadable` via :989-991, executed twice). All are safe — nothing
mutated, world intact — and clear by deleting/repairing the named file; the strictness is defensible, the
missing remedy text is the finding.

## SCENARIOS

**Happy rotation A→B→A (executed).** `dayz_test_run` with modset B classifies `seal_changed`; `rotate_storage`
reserves D=`storage_1.modset-<stamp>-<A8>` plus K, writes journal P, renames the A-world W→D, advances P→S,
renames marker A→K, publishes N(B), Q, renames J→C. The run record, the audit row `lifecycle_storage_rotated`,
and the `dayz_test_run` answer carry `storage_rotated=true`, `storage_backup=D`,
`storage_reset_notice=mission_world_and_character_reset`; the B engine then creates a fresh W. Launch A: marker
B≠A → second rotation, B-world→D2, marker A; launch A again: `seal_matches`, `rotated=false`. After every step
the displaced world was byte-intact under its backup (verified), and the status row re-delivers the observation
to later calls only on run_id + `launch_operation_id` match.

**Crash at each phase, then a launch (executed).** Cuts after W→D (phase still P), after S, after M→K, after N,
after Q: a relaunch with the SAME seal recovers each to a completed journal, reports
`rotated=true reason=recovered_storage_moved` with the journal's D, world intact, marker canonical A. A relaunch
with a DIFFERENT seal — the D3 poisoning class — finishes the old transaction and then publishes the caller's
seal BEFORE any spawn: marker verified == B at return, and the later A launch rotates the B-created world into a
fresh backup. No launch ever runs against a marker naming another modset. A crash during recovery re-enters the
walk on the frozen state; the depth-2 fixed-point gate agrees and passed here.

**Operator deletes or restores a backup by hand (executed).** Rename-back during P: absorbed (journal completed
as abort, fresh rotation, launch allowed; two completed journals remain, harmless). The same rename-back during
S, a copy-restore during Q, and deleting D during S each produce the E1 permanent refusal on every later launch,
both seals, until the active journal is removed by hand — after which the next launch reports `rotated=true`
with the world preserved in a new backup, or `seal_only` fresh-world when D was the deleted copy; no corruption,
no deletion at any point. The completed-journal side degrades as documented: a crash after C with W not yet
recreated replays as `pending_completed_rotation` (`rotated=true`, D named) for the same seal, and two matching
completed journals report unknown instead of guessing (executed via `_pending_completed_rotation`).

## NOT VERIFIED

- Real NTFS identity behaviour (8.3 aliases, sharing violations from open handles, indexer/AV locks) and
  power-loss/directory-entry durability: not executable in this sandbox; `CONTEXT.md` lists the same gap.
- The launch-path envelope around the refusal (process_lifecycle.py:4240-4290: `_retire_minted`,
  `manifest.replace`→`manifest_failed`, hint attach) and the worker's `launch_operation_id` emission: verified by
  reading only; no daemon or MCP tool was run (boundary).
- `recovery_finish_failed` / `recovery_seal_publish_failed` OSError branches: not fault-injected by me; their
  post-failure states (journal completed, marker N or X, next call self-heals) are reasoned from :905-972, plus
  the model gate's refusal negatives.
- The `range(8)` bound in `_finish_recorded` (:907) against the 5-mutation maximum of the selector: verified by
  reading, not by a longest-chain execution.
