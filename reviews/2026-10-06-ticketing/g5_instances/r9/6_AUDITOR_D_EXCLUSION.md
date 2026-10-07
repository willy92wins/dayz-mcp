# AUDIT.md — DZ-R9 step 6, angle D: exclusion and concurrency after the g5 fixes

Re-verified every "this is safe because X" of batch g5r9_locks (A6, A7, B2, B3) and g5r9_journal's
installer lock (A3). All behavior claims below were executed with the project venv
(`tools/.venv-mcp/Scripts/python.exe`, Python 3.14.3, Windows) in temp directories, from this tree
(`ws/tools` on `sys.path`); scratch under `_scratch/`, deleted before finishing. Measured facts used
throughout: `msvcrt.locking` byte locks are exclusive across two handles **in the same process** too
(second `LK_NBLCK` fails, errno 13); `DeleteFile` on a file opened without `FILE_SHARE_DELETE` fails
(errno 13); `LK_NBLCK` works on a zero-length file.

## FINDINGS

### D1 — P3 — A relative `register_transaction(journal_root=...)` splits the installer lock per cwd
```python
# tools/install_mcp.py:1204-1207
def _registration_lock_path(journal_root: Path) -> Path:
    # Sibling of the journal directory, under that directory's parent:
    # <LOCALAPPDATA>\DayZ_MCP\registration-transaction.lock
    return journal_root.parent / f"{journal_root.name}.lock"
```
with `root = Path(journal_root) if journal_root is not None else _default_registration_journal_root()`
(`install_mcp.py:1474`) and `path = _registration_lock_path(journal_root)` (`:1249`). A7 refuses a
relative `DAYZ_MCP_SHARED_ROOT` (`server_cli.py:163-164`, executed: `OSError: relative_shared_root`).
The installer lock does the opposite for its own parameter: a relative `journal_root` is accepted and
the lock resolves against the process cwd. Two callers passing the same relative root from two
directories take two different lock files, so "One installer at a time, before discovery"
(`install_mcp.py:1475-1476`) does not hold between them; they also get different journal dirs, so no
shared recovery record exists. The production caller passes no `journal_root`
(`install_mcp.py:1735-1745`), so today only the test seam can do this — same defect class A7 fixed,
one layer down.
Executed: RAN. Temp dirs, fake providers: A acquired `rel-reg-journal.lock` relative to cwd `a`; from
cwd `b`, `register_transaction(..., journal_root=Path("rel-reg-journal"), lock_timeout_s=1.0)` ran to
completion in 0.02 s (4 provider calls) while A still held. `_registration_lock_path` returned
`rel-reg-journal.lock`, not absolute.

### D2 — P3 — The PowerShell `-Register` path joins no installer lock and writes no journal
```powershell
# tools/install-mcp.ps1:900-906, 924-928 (excerpt)
if ($Register) {
  $registrationDecision = Get-RegistrationReplaceDecision ...
  ...
  & claude mcp add dayz-mcp -s user -- $VenvPython @claudeArgs
```
The one cross-process installer lock exists only in `install_mcp.py:1477-1490`
(`_acquire_registration_lock`, executed: contender gets `registration_busy` at the configured limit
with zero provider calls). `install-mcp.ps1 -Register` (`:900-947`) runs `claude mcp add/remove` and
`codex mcp add/remove` with no lock, no journal, and no recovery: killed mid-way it leaves hosts half
registered with nothing to replay. Concurrent with a Python registration it mutates the same
`.claude.json`/`.codex` files (`claude mcp add` rewrites `.claude.json`; the Python side's
`apply_host_timeouts`, `install_mcp.py:1586`, writes the same files) — two writers the lock cannot see.
A3's spec only required the journal for the Python path and the Claude-add rollback for the PS path
(`install-mcp.ps1:42-91` seam, executed by the project's own test), so this is per-spec — but the
"one installer at a time" property is true only among Python installers.
Executed: NOT RUN (real registrations forbidden). Code reading of `install-mcp.ps1:900-947`; the lock
is referenced nowhere in the script (grep).

### D3 — P3 — B3's start scan is a sample in time; a legacy writer started after it coexists
```python
# tools/dayz_mcp/identity_migration.py:1598-1606
        if settled is not None:
            if _blocking_pids(
                allowed_current_identity,
                allowed_launch_ancestor_identity,
                scan_fn,
                state_token,
            ):
                raise RunsBackupGateError("dayz_mcp_process_present")
            return settled
```
The scan (`scan_dayz_mcp_processes`, `:918-1022`) sees only processes alive at that instant. The
root-writer lease (`instance_context.py:179-230`) excludes only lease-aware writers; a legacy
same-root writer that starts after the scan takes no lease and is never re-checked — grep shows no
second scan anywhere between the gate and the state-root writes (`runtime_state.py`,
`process_lifecycle.py`, `daemon.py`: no `scan_dayz_mcp_processes`). Result: the exact two-writers-on-
one-`runs.json` scenario B3 closes can recur if the legacy writer starts in the window after the scan.
The write-gate branch has the same window plus the whole copy duration. R9-TABLE already schedules the
real fix (retire the legacy tree); this records the residual, which the fix's own comment
("still refuses the start", `:1585-1589`) states more strongly than the code can deliver.
Executed: PARTIALLY RAN. With fakes: settled + blocker pid → `dayz_mcp_process_present`; settled +
accredited other-root writer → starts; settled + live listener + empty scan → returns settled (the
listener is deliberately not re-checked, `:1585-1589`; safe only because any same-root listener is
itself a classified writer). The full live race needs a real legacy daemon — not run.

## LOCK TABLE

| Lock | File | Share mode | Wait | Owner | Release on success / error / death |
|---|---|---|---|---|---|
| Root-writer lease, byte 1 of `<root>/.daemon-startup.lock` | instance_context.py:179-256 via `open_lock_file` (server_cli.py:112-145) | r+w, no delete | none (`LK_NBLCK`) | `_OwnerToken` kept by the lease; dict holds strong ref (`:162`, `:227`); in-process reentry by `is` identity | unlock+close on last `release()` / unlock+close in error paths; unreleased lease wedges its own process until exit (M11) / OS drops |
| Startup election, byte 0 of the same file | identity_migration.py:346-407 via `open_lock_file` | r+w, no delete | none; loser yields False | process (byte-range lock); byte 0 coexists with lease byte 1 (executed) | unlock+close in `finally` / close on error / OS drops |
| Runs gate, `<migration>/.runs-v1.lock` | identity_migration.py:410-458 via `open_lock_file` | r+w, no delete | none; second starter fails `runs_backup_lock_unavailable` | process | unlock+close in `finally` / close+raise / OS drops |
| Box admission, `<shared>/box-admission.lock` | box_admission.py:23-58 via `open_lock_file` | r+w, no delete | none; busy → `box_admission_busy` (process_lifecycle.py:3558-3564) | process + thread depth (`:84-103`) | close in `_release_os` / close in `except OSError` / OS drops |
| Shared build locks, `<shared>/build-locks/<sha>.lock` (one per target/temp, sorted order) | server_cli.py:244-284 (sync), :287-345+ (async) via `open_lock_file` | r+w, no delete | poll 0.05 s until `wait_s` (default 900 s; sealed per request, worker ignores env, dayz_test_worker.py:307-325), then `BuildLockBusy` → `build_busy` (dayz_test_worker.py:855-856) | process | `finally` unlocks every held byte (`:282-284`); partial acquisition released on timeout **and** on cancel (executed) / same / OS drops (executed: killed holder, contender acquires in 0.01 s) |
| Installer registration lock, `<DayZ_MCP>/registration-transaction.lock` | install_mcp.py:1246-1267 (open :1210-1233, own CreateFileW) | r+w, no delete | poll 0.02 s until `lock_timeout_s` (default 120 s), then `registration_busy` before any discovery/mutation (executed: zero provider calls) | `_RegistrationLockToken` (`:1179`), per acquire | `finally: held.release()` (`:1489-1490`) incl. `RegistrationCrash` / LK_UNLCK failure swallowed, handle closed (`:1188-1201`) / OS drops; byte-level exclusion verified across processes and server names (executed) |

All six: two holders could not coexist in any executed scenario — two processes, two handles in one
process (byte locks exclusive per handle), delete-and-recreate while held (fails, errno 13), or a
collected owner whose id is reused (`is` identity, strong ref; project test
`test_collected_owner_id_does_not_admit_a_new_object` passes). A dead holder never wedges any of them.
Not converted (pre-g5r9 locks opened with plain `os.open`, i.e. share-delete, no in-tree deleter,
outside this batch's scope): inbox.py:181, knowledge.py:521, runtime_state.py:410.

## NOT VERIFIED

- Whether a killed worker orphans its native AddonBuilder child, which would keep writing the build
  target after the build lock died with the worker (M9-adjacent). Running any builder is forbidden.
- Cross-path PS1/Python concurrent registration (D2) end-to-end — needs real `claude`/`codex` runs.
- The full live D3 race with a real legacy daemon — needs a daemon run; forbidden.
- `psutil_unavailable` on a settled machine now fails the start (fail closed, `:1102-1123`); the daemon
  needs psutil anyway (`daemon.py:235-242`), so impact assumed nil — not executed end-to-end.
