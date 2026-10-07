| claim | snippet_matches_file | inference_holds | evidence (lines pasted or scenario run) |
|---|---|---|---|
| C1 | **Yes** | **Yes**, for the described retry before replacement storage exists. | Recovery produced journal seal A, marker seal X; replay for X returned `None`; lifecycle recorded `storage_rotated=False`. |
| C2 | **Yes** | **Yes**, with a distinction for subsequent retries. | After interrupting the derived rotation before the world move, three retries returned `backup_name_collision`. The first collided with the derived completed journal; subsequent retries collided with the original completed journal. |
| C3 | **Yes** | **Yes**, while that directory remains. | Absent storage selects `seal_only`; publication attempts to replace a directory with a file; the caller catches the resulting `OSError`. |
| C4 | **Yes** | **Yes** | A 60,001-byte nested JSON caused `prepare_storage` to raise `RecursionError` twice for each location: active journal and marker. |
| C5 | **Yes** | **Partly; false if journal removal is claimed to be necessary.** | All six phase/action combinations refused both tested seals. Restoring the physical layout allowed recovery without manually removing the journal. |
| C6 | **Yes** | **Partly; false as stated about the public hint.** | All four named refusals mapped to `storage_recovery_required`, with the precise reason in the audit. The lifecycle hint exists, but the worker discards it before the public result. |
| C7 | **Yes** | **No, as universally stated.** | A `.json.bak` file blocked launch; `storage_1.modset.rotation.garbage.completed.json` was ignored and launch was allowed. |

Scenarios used the actual storage module and lifecycle functions extracted from their source AST, with filesystem operations replaced by an in-memory model. C3’s filesystem exception was modeled; C4 used the actual JSON decoder. No files, processes, daemons, MCP tools, or real missions were changed. Below, **S** means `tools/dayz_mcp/dayz_test_storage.py`, **L** means `tools/dayz_mcp/process_lifecycle.py`, and **W** means `tools/dayz_mcp/dayz_test_worker.py`.

**C1.** Recovery completes the journal with its original `new_seal=A`, then publishes X without changing that journal. Replay filters by the journal’s `new_seal`, so it skips A when called with X. The ordinary preparation result is then non-rotation, copied into the provisional run and persisted before spawn. The scenario returned `recovery_rotated=True`, `pending_X=None`, and `retry_storage_rotated=False`; replay for A still found the rotation.

```python
# S:943-945
        if call_seal != str(document["new_seal"]):
            try:
                _publish_marker(mission, call_seal, project)

# L:165-168
            or document.get("phase") != dayz_test_storage.PHASE_MARKER_PUBLISHED
            or document.get("new_seal") != seal
        ):
            continue

# L:3315
            pending = _pending_completed_rotation(mission, seal)

# L:3372
        provisional.storage_rotated = False

# L:4247
                            self.manifest.replace(provisional)
```

**C2.** Aborting preserves the prepared journal under its completed name. Recovery then derives a second transaction ID from the caller’s fixed ID. If that second transaction is interrupted before moving storage, the next recovery aborts it and tries that same derived ID, which now collides. Afterward there is no active journal, so subsequent retries use the original ID instead—and its completed journal also exists. Thus the persistent collision holds, although later retries do not all collide through the derived ID.

```python
# L:3297-3299
                txid=hashlib.sha256(
                    (run_id + ":" + seal).encode("utf-8")
                ).hexdigest()[:32],

# S:869-871
    if action == "abort":
        _complete_journal(mission, str(document["txid"]))
        return document

# S:529-533
def _complete_journal(mission: str, txid: str) -> None:
    _rename_strict(
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_SUFFIX),
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_COMPLETED_SUFFIX),
    )

# S:1151
        rotation_txid = derived_txid(txid)

# S:1099
    return hashlib.sha256(f"{txid}:retry".encode("ascii")).hexdigest()[:32]

# S:1059-1062
        ntpath.join(mission, JOURNAL_PREFIX + txid + JOURNAL_COMPLETED_SUFFIX),
    )
    if any(_entry_kind(path) != "absent" for path in reserved):
        return _blocked("backup_name_collision", seal)
```

**C3.** With no storage directory, the decision is `seal_only` regardless of marker classification. A directory at the marker path cannot satisfy the identical-file shortcut, so publication reaches `os.replace`, attempting to replace a directory with a regular file. Its `OSError` maps to `storage_rotate_failed`. Nothing in this path removes the obstructing directory, so unchanged retries repeat the failure.

```python
# S:227-228
    if not storage_present:
        return Decision(DECISION_SEAL_ONLY, "storage_absent")

# S:599-604
def _publish_marker(mission: str, seal: str, project: str) -> None:
    path = ntpath.join(mission, MARKER_NAME)
    payload = _canonical_marker(seal, project)
    if _entry_kind(path) == "file" and _read_bytes(path) == payload:
        return
    _publish_payload(path, payload, replace=True)

# S:582-583
        if replace:
            os.replace(temporary, path)

# S:1169-1170
    if decision.action == DECISION_SEAL_ONLY:
        _publish_marker(mission, seal, project)

# L:3301-3302
        except (dayz_test_storage.StorageError, OSError):
            return "storage_rotate_failed"
```

**C4.** The parser catches decoding and JSON syntax errors, but not `RecursionError`; the lifecycle catch likewise excludes it. On this Python 3.14.3 build, `b'[' * 30000 + b'0' + b']' * 30000` is 60,001 bytes and triggered `RecursionError` through both journal and marker preparation, on two consecutive attempts each. This establishes an escaping exception, not a process crash.

```python
# S:285-291
def _parse_json(raw: bytes) -> object | None:
    if not raw or len(raw) > 65_536:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

# S:989 — active journal
    document = _valid_journal(_parse_json(raw), txid)

# S:371 — marker
    document = _parse_json(_read_bytes(path))

# L:3301-3302
        except (dayz_test_storage.StorageError, OSError):
            return "storage_rotate_failed"
```

**C5.** The unchanged states described do refuse independently of the requested seal: copying back leaves both directories present; renaming back leaves the required backup absent; deleting the backup leaves either both absent or the required backup absent. Both phases and all three operations returned `journal_state_impossible` for A and X. However, reversing the physical alteration allowed `recovered_storage_moved` while retaining the active journal for recovery to complete normally. The shipped hint below says to inspect files and names no removal remedy; the targeted documentation search found no such instruction.

```python
# S:651-657
    if w == "dir" and d == "dir":
        return "refuse"
    if w == "absent" and d == "absent":
        return "refuse"
    phase = str(document["phase"])
    if phase in {PHASE_STORAGE_MOVED, PHASE_MARKER_PUBLISHED} and d != "dir":
        return "refuse"

# S:919-920
            if action == "refuse":
                return _blocked("journal_state_impossible", call_seal)

# S:1146-1147
        if recovered is not None:
            return recovered

# L:1244-1246
        "no sequence produces). Nothing was launched. Inspect the "
        "storage_1.modset.rotation.* files next to storage_1 before retrying; "
        "no data was deleted -- v1 renames and never removes."
```

**C6.** The exact four refusal reasons are passed to the lifecycle audit, while the storage caller returns the generic code; all four scenarios confirmed that mapping. The lifecycle adds a static hint, including the claim that the next call reconciles a failed transaction. But the worker carries only the error code, discarding that envelope and hint; the public result’s remediation is normally `None` for these errors (`dayz_test_tool.py:1934–1938`). Also, “every” is too strong: failed settlement persistence can replace the public error with `manual_cleanup_required` (`L:3724–3729`), and an audit-write failure can lose the detailed reason.

```python
# L:3329
            reason = result.reason if isinstance(result.reason, str) else ""

# L:3332-3336
            written = self._audit(
                "lifecycle_storage_recovery_required",
                None,
                reason,
                "rejected",

# L:3341
            return "storage_recovery_required"

# L:4263-4265
                        hint = _STORAGE_ROTATE_HINTS.get(storage_error)
                        if hint is not None:
                            settled["hint"] = hint

# L:1238-1239
        "name and an active journal beside storage_1, which the next call "
        "reconciles. Nothing is ever deleted -- v1 renames only."

# W:837-840
    code = result.get("error")
    if not isinstance(code, str) or code not in LIFECYCLE_REJECTION_CODES:
        return None
    return code

# W:968-969
        if not _successful_run(result, target_run_id, "RUNNING"):
            raise _failed(_lifecycle_rejection(result) or "worker_failed")
```

**C7.** The `.bak` example does block unchanged retries, but the universal assertion fails because completed-suffix names are skipped before grammar validation—even when they contain an invalid transaction ID or invalid JSON. The scenario with `storage_1.modset.rotation.garbage.completed.json` returned `launch_allowed=True`, reason `storage_absent`. Removing the offending entry is also not uniquely necessary: renaming it outside the scanned namespace avoids the same check.

```python
# S:1009-1016
    for name in entries:
        if not name.startswith(JOURNAL_PREFIX):
            continue
        if name.endswith(JOURNAL_COMPLETED_SUFFIX):
            continue
        if _JOURNAL_ACTIVE.fullmatch(name) is None:
            malformed = True
            continue

# S:1134-1135
    if malformed:
        return _blocked("journal_name_invalid", seal)
```