# AUDIT — lane D: persistence & atomic flow of r9b storage rotation on a real Windows volume

Method: every I/O step of `dayz_test_storage.py` read against NTFS semantics; the crash cuts and the hostile-input
bounds executed with the project venv (`tools/.venv-mcp`, Python 3.14) on real directories under `_scratch/`
(since deleted). Cut states were planted with the module's own primitives (`_write_json_atomic`, `_complete_journal`,
`_publish_payload`, real renames); every recovery/verify step was the real `prepare_storage` entry point.

## FINDINGS

### D1 — P2 — a second crash in the prepared window wedges every retry that reuses the launch identity (`backup_name_collision`)

`tools/dayz_mcp/process_lifecycle.py:3295-3299`:
```python
                # Derived, never minted: a retry of the same launch names the
                # same journal, and no clock or randomness enters the id.
                txid=hashlib.sha256(
                    (run_id + ":" + seal).encode("utf-8")
                ).hexdigest()[:32],
```
`tools/dayz_mcp/dayz_test_storage.py:1148-1151`:
```python
        # The reconciled transaction already owns the journal names built from
        # `txid`, so a rotation decided after it gets its own derived id. Same
        # call, same input, deterministic: no clock and no randomness here.
        rotation_txid = derived_txid(txid)
```
with the reservation `dayz_test_storage.py:1055-1062`
```python
    reserved = (
        ntpath.join(mission, backup),
        ntpath.join(mission, marker_backup),
        journal_path,
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_COMPLETED_SUFFIX),
    )
    if any(_entry_kind(path) != "absent" for path in reserved):
        return _blocked("backup_name_collision", seal)
```
and completion `dayz_test_storage.py:529-533,887-889` (active journal renamed to `....completed.json`, kept forever).

Reasoning. `derived_txid` (`:1095-1099`) is a pure function, so the derived id `T2` is the same on every recovery of
the same `(run_id, seal)`. The first recovery that aborts a prepared journal creates `C_T` and re-rotates as `T2` — fine.
If that `T2` rotation is killed in the same window (journal published, world not yet moved — the whole 9-step atomic
publish, B10–B20), the next recovery aborts it into `C_T2` and re-rotates with `derived(T) == T2` again: the reserve
finds `C_T2` present → `backup_name_collision`, `launch_allowed=False`. The attempt after that has no active journal,
rotates with raw `T`, and hits `C_T` → blocked too. Every later retry of that launch identity is refused
(`storage_recovery_required`); only an identity hashing to a fresh txid heals. The in-memory model never reaches this
because each recovery generation there uses a fresh literal (`test_storage_rotation_model.py:691,723,743`:
`"2"*32, "3"*32, "4"*32`), so the same-txid re-rotation is untested. No data is ever at risk (the world stays in `W`
throughout); the defect is a persistent refusal plus a comment that asserts the opposite of the behavior.

Executed (venv, real dirs): plant `J_T(prepared)` with `W` intact; real `prepare_storage(seal B, txid T)` → abort +
rotation `T2` completes. Plant the second generation by the recovery's own steps (`_complete_journal(T)` then
`_journal_document(txid=T2)` + `_write_json_atomic`), world intact. Then real calls, txid `T`:
attempt 3 `False/backup_name_collision`, attempt 4 `False/backup_name_collision`, attempt 5 idem; a call with txid
`"2"*32` → `True/seal_changed` (heals, world moved to a fresh backup, marker published).

### D2 — P3 — `seal_only` publish onto a non-file marker raises instead of returning a blocked result; the refusal then repeats with the wrong name

`tools/dayz_mcp/dayz_test_storage.py:599-604`:
```python
def _publish_marker(mission: str, seal: str, project: str) -> None:
    path = ntpath.join(mission, MARKER_NAME)
    payload = _canonical_marker(seal, project)
    if _entry_kind(path) == "file" and _read_bytes(path) == payload:
        return
    _publish_payload(path, payload, replace=True)
```
called unguarded at `:1169-1170` (`if decision.action == DECISION_SEAL_ONLY: _publish_marker(...)`); the replace is
`:582-583` (`os.replace(temporary, path)`). With `storage_1` absent and the marker name occupied by a directory,
`read_marker` reports `absent`, the decision is `seal_only`, and `os.replace` file→directory raises
`PermissionError` (measured: `WinError 5`). The caller maps any `OSError` to `storage_rotate_failed`
(`process_lifecycle.py:3301-3302`), so every launch fails with a reason that names the wrong subsystem, and nothing
self-heals — while the rotate branch handles the same object cleanly (`:1039-1041`, `marker_type_unsupported`).
Reachable wherever a directory named `storage_1.modset.json` exists; recovery is manual.

### D3 — P3 — `_parse_json` misses `RecursionError`: a hostile ≤64 KiB journal or marker crashes `prepare_storage` on every launch

`tools/dayz_mcp/dayz_test_storage.py:285-291`:
```python
def _parse_json(raw: bytes) -> object | None:
    if not raw or len(raw) > 65_536:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
```
A 60 000-deep `[[[...` payload (60 KB, under the cap) raises `RecursionError`, which is neither caught class; it
escapes `_reconcile_journal` (`:989`, no try) and `prepare_storage`, and the caller catches only
`(StorageError, OSError)` (`process_lifecycle.py:3301-3302`) — no blocked `RotationResult`, no audit row. Measured:
`prepare_storage` raised `RecursionError` ("Stack overflow ... decoding a JSON array") with an active journal so
crafted; every subsequent launch on that mission repeats it until the file is removed. The read itself is also
unbounded before the cap (`_read_bytes` `:280-282` loads any size, the 64 KiB test runs after). Truncated journals
are handled well (measured: `journal_unreadable`, nothing mutated). Fix is one clause: catch `RecursionError` (or
`Exception`) in `_parse_json`.

## CRASH MATRIX

Producer cuts (B-numbers per SPEC_R9B_IMPL); "next prepare" = the real entry point on the planted disk state.

| Cut (between) | On disk (W=world D=backup M=marker K=marker-backup J/C=journal T=temp) | Next `prepare_storage` does | Verified |
|---|---|---|---|
| B10–B14 temp create/write | T empty/partial; J,C,W,M,K untouched | S0; T invisible to the scanner (`tmp-` prefix fails `_JOURNAL_ACTIVE` and `JOURNAL_PREFIX`) | model; read |
| B15–B19 temp→J | J=prepared; W intact | abort: J→C, classify, re-rotate (new derived txid — see D1) | executed, real NTFS |
| B20/B21 W→D rename, phase P | J=P; W absent; D=world | advance P→S (atomic rewrites), then preserve/publish/complete | model; rename exercised |
| B27–B33 M→K / none | J=S; D; M=E or ∅; K ∅/E | `preserve` (rename M→K) or straight to publish N | model; read |
| B40–B48 N publish | J=S; M=N; K | `advance_q` (N byte-check `:602` makes it idempotent) | model; read |
| B49–B53 phase Q | J=Q; M=N; K | complete + reseal to caller's seal if different | executed end-to-end: world intact in D, K byte-equal, marker→X, `recovered_storage_moved` |
| B54 J→C | C only; W absent; marker N | no active journal → classify: seal match ⇒ reuse/`seal_only`; D stays until admin prune | executed (`storage_absent` path) |
| B55 cleanup unlink | leftover T | ignored; later temps use `xb` counter (pid reuse safe) | read |
| any cut inside recovery R1–R9 | subset of the above | re-observed from disk each call (`_observe_pair` re-stats; no memory) → composition holds | model + Q-cut executed |

NTFS-specific facts applied: every artifact is a sibling of `W` in the mission dir, so no rename is ever cross-volume
and `os.replace`/`os.rename` stay atomic metadata operations; the reserve check via `os.lstat` sees case-variants and
trailing-dot twins (Win32 normalization), and journal-declared aliases are refused by the `_win32_file_identity` dedup
(`:495-499`); `os.listdir` yields long names only, so 8.3 aliases cannot dual-name a journal. DayZ holds handles only
after spawn and rotation is strictly pre-spawn; AV/indexer/OneDrive handle conflicts surface as `OSError` → blocked
result or the caller's catch — except the two unguarded raises (D2, D3). No directory fsync exists anywhere (file
fsync only, `:339-340,:580-581`): on NTFS metadata is journaled, data is flushed before the rename, so a power cut
leaves old-or-new name with full payload — worst case one duplicated rotation, never a torn publish. There is no tree
copy anywhere (v1 renames whole directories), so partial-directory-copy states cannot exist.

Input bounds of persisted documents: journal — exact key set per schema, hex txid/seal regexes, phase enum, project
1..64, plain-name rule, reserved-name identity dedup, schema-2 state↔hash coupling (`:441-519`); every violation
lands in a blocked result (`journal_unreadable` / `journal_state_impossible`), nothing mutated — measured for the
truncated case. Marker — exact key set + seal regex; oversized/non-JSON degrades to `present_invalid` → rotate
(fail-safe); the recursion hole is D3. Completed journals read by `_pending_completed_rotation`
(`process_lifecycle.py:139-195`) are revalidated and backup-checked `isdir`; unreadable ones are skipped. The
observation-log round trip (D4) preserves `launch_operation_id` writer→loader→consumer
(`:1913-1914`, `:281-285`, `dayz_test_tool.py:1970-1972`).

## NOT VERIFIED

- Real power-loss/crash injection on NTFS: the durability column is Win32/NTFS journaling reasoning, not a measured
  machine crash; the design's own NOT VERIFIED concedes the same.
- OneDrive placeholder semantics (hydration on read, sync locks during rename): the probe volume was plain NTFS; the
  production tree lives under OneDrive. Placeholder directories would also take the D2 path.
- Third-party handle contention (AV/indexer) and DayZ's own handle mode were not simulated with live holders.
- D1's trigger precondition — a caller that repeats `(run_id, seal)` after its run row is gone — depends on manifest
  row durability and the pinned-`new_run_id` contract; those belong to the lifecycle lanes and were not executed.
- `os.replace` directory→file and symlink-marker variants; junctioned `storage_1` (fails closed as
  `storage_not_a_directory`, read but not executed).
- Sealed-lock regeneration, packaged-modules identity, in-game acceptance: outside this lane.
