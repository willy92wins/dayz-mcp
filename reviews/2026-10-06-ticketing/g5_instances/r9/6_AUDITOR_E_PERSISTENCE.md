# DZ-R9 step 6 angle E: persistence and recovery of the two journals (post-g5 re-verification)

Focus: `tools/dayz_mcp/host_config.py` host-config journal (manifest.next/manifest.json, restoring phase,
A1/A2) and `tools/install_mcp.py` per-identity registration journal (staging/next/published, lock, A3).
Method: every mutation boundary read in order; kill simulated at the boundary with the project venv
(`.../tools/.venv-mcp/Scripts/python.exe`, Windows, Python 3.14.3) in temp directories with fake providers;
`HostConfigCrash`/`RegistrationCrash` or patched syscalls stand in for SIGKILL exactly at the boundary.

## FINDINGS

### E1 — P1 — An empty host-config journal directory wedges every installer for ever
`host_config.py:1118-1120`:
```python
def _cleanup_journal(journal: Path) -> None:
    if journal.exists():
        shutil.rmtree(journal)
```
and `host_config.py:1292` (`_mkdir_restricted(journal)` before any manifest write).
Reasoning: two kill points leave a journal directory with no `manifest.json` and no `manifest.next`:
(a) kill after `_mkdir_restricted(journal)` (apply_host_timeouts:1292) returns and before
`_persist_manifest` opens `manifest.next` (:1295→:966); (b) kill inside `shutil.rmtree` during
`_cleanup_journal` after the unlink of `manifest.json` but before the rmdir (journal holds exactly one
file once committed/restored). `_recover_if_needed` treats a non-existent journal as "nothing to do"
(:1174-1175) but an EXISTING empty one goes to `_load_manifest_publication` `host_config.py:1007-1017`:
neither file exists → `raise OSError` → `registration_journal_invalid`. No code path ever discards an
unparseable/absent publication, so every later `apply_host_timeouts` — for every instance, because
`_default_journal_root()` (:911-915) is one shared directory and the registration lock serialises all
installers through `_recover_pending_host_configs` (install_mcp.py:1518) — refuses with
`registration_journal_invalid` until a human deletes `%LOCALAPPDATA%\DayZ_MCP\host-config-transaction`.
This is the exact wedge class A2 claims to have removed ("never left as `registration_journal_invalid`
forever"): the empty directory is a byte-exact state the journal itself produces.
Scenario (ran, see S1/S3): kill at both boundaries → run 2 and run 3 both raise
`registration_journal_invalid`; manual deletion is the only exit. Contrast: the registration journal
survives the same state — `_load_registration_manifest` (install_mcp.py:1346-1352) returns `None` when
neither file exists and the run proceeds (ran, S4a).

### E2 — P1 — A torn/empty `manifest.next` with no `manifest.json` wedges the shared journal for ever
`host_config.py:963-967`:
```python
def _persist_manifest(journal: Path, manifest: dict[str, object]) -> None:
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = journal / "manifest.next"
    _write_private(temporary, payload)
    os.replace(temporary, _manifest_path(journal))
```
with `host_config.py:944-956` (`_write_private`: `os.open(..., O_TRUNC)` then `os.write`).
Reasoning: kill the process after `os.open` in the FIRST publication truncated/created
`manifest.next` but before `os.write` lands (the window is a handful of bytecodes inside one Python
call; A2's fix covers only the next instruction onward, i.e. a *complete* `manifest.next`). On disk:
journal dir + 0-byte (or partial) `manifest.next`, no `manifest.json`, host files untouched. Next run:
`_load_manifest_publication` (:1010-1012) reads the pending file, `json.loads` raises
`JSONDecodeError` → `registration_journal_invalid` — permanently; recovery never reaches the A2
replay/discard logic because loading itself fails. install_mcp fixed precisely this state for the
registration journal with a staging file plus `_discard_incomplete_registration_publication`
(install_mcp.py:1299-1309, :1317-1332: "Drop a first-write fragment"); host_config got no staging and no
discard, so the A2 regression ("a crash injected in that `os.replace`, then two fresh runs succeed") is
one instruction short of the real crash window. Same shared-wedge blast radius as E1.
Scenario (ran, S2): 0-byte `manifest.next` → run 2 and run 3 `registration_journal_invalid`.

### E3 — P3 — Journal recording a host path that no longer exists: named refusal, permanent shared wedge
`host_config.py:1048-1053`:
```python
try:
    path = Path(value["path"]).resolve(strict=True)
    ...
except (TypeError, ValueError, OSError):
    raise HostConfigError("registration_journal_invalid") from None
```
Reasoning: crash mid-transaction leaves the shared journal with `manifest.json` recording
`~\.claude.json`; the user (or a tool) then deletes that file. The next installer run of ANY instance
calls `_recover_pending_host_configs` → `_recover_if_needed` → `_decode_manifest_file` →
`registration_journal_invalid` before any mutation, for ever. Refusal is named and no data is lost, but
the only exit is hand deletion — the flagged "journal that wedges every later installer" class.
Scenario (ran, S5): journal prepared for a temp claude file, file deleted → run 2/3
`registration_journal_invalid`.

### E4 — P3 — Host config replaced between crash and recovery: `registration_recovery_conflict` for ever
`host_config.py:1189-1190`:
```python
if any(handles[role].identity() != decoded[role][1] for role in decoded):
    raise HostConfigError("registration_recovery_conflict")
```
Reasoning: the identity is volume-serial + file-id (:703-715). `~/.claude.json` is rewritten by Claude
Code itself; an atomic rewrite (temp + replace, or delete + create) changes the file id. A crash mid
host-transaction followed by such a rewrite makes recovery refuse for ever — the shared journal again
needs hand deletion. Refusing is the safe direction (the recorded original belongs to the old file
object); the defect is permanence with no self-healing exit and no distinction from a wedged state.
Scenario (ran, S6): journal in restoring_original, claude file deleted+recreated with different
content → run 2/3 `registration_recovery_conflict`.

### E5 — P3 — A round-2 shared-directory registration journal is silently ignored, never recovered
`install_mcp.py:1291-1296` and `install_mcp.py:1505-1507`:
```python
journal = journal_root / server_name
_discard_incomplete_registration_publication(journal)
loaded = _load_registration_manifest(journal)
```
Reasoning: this code only ever reads `journal_root/<server_name>/manifest.json`. A journal written by
the round-2 one-shared-directory layout (`journal_root/manifest.json`) — the shape the angle brief
names — is never discovered: no recovery of its recorded `previous` registrations, no host repair
through its recorded `host_configs`, no cleanup; the stale file stays forever. A fresh run of the same
identity happens to repair the registrations as a side effect, but a crash journal's record is dropped
without ever being read (the "never silently drop a record" bar).
Scenario (ran, S7): valid prepared manifest at `journal_root/manifest.json`; run proceeds, file
untouched and unread.

### E6 — P3 — PowerShell rollback: remove exit code unchecked; replace-path failure loses the previous Claude registration
`install-mcp.ps1:47-58`:
```powershell
function Undo-DayZMcpClaudeRegistration {
  param(
    [string]$Name,
    [scriptblock]$RemoveCommand
  )
  if ($null -ne $RemoveCommand) {
    & $RemoveCommand $Name
    return
  }
  $removeName = if ($Name -eq 'dayz-mcp') { 'dayz-mcp' } else { $Name }
  & claude mcp remove $removeName -s user
}
```
Reasoning: (a) the real rollback never checks `$LASTEXITCODE` — a failed `claude mcp remove` is
silently swallowed and the throw still says "Stopped without a Codex remove", leaving Claude
registered (or half-registered) with no named error; (b) in the `-ReplaceExistingRegistration` path the
previous registration was already removed (:917-922) before the adds, so a Codex-add failure rolls back
only "what it added" and the user ends with no Claude registration at all — the durable Python path
instead restores `previous` (`_rollback_registrations`, install_mcp.py:1120-1137, and recovery
:1400-1414); (c) the Codex `mcp remove` exit code in the replace path (:933-938) is also unchecked. Not
executed live (registration of real clients is out of bounds); verified by reading the seam and the
`-ValidateRegistrationRollback` self-test, which only exercises the fresh-add path with a scripted
RemoveCommand.

## CRASH TABLE

| # | Boundary (kill point) | State on disk | Next run's outcome | Run killed during that recovery |
|---|---|---|---|---|
| 1 | host: after `_mkdir_restricted` (1292), before manifest write | empty journal dir, hosts original | `registration_journal_invalid` every run (E1a wedge) | same wedge, no progress possible |
| 2 | host: inside `_write_private`, after O_TRUNC, before write (966) | 0-byte `manifest.next`, no `manifest.json`, hosts original | `registration_journal_invalid` every run (E2 wedge) | same wedge |
| 3 | host: after first `manifest.next` complete, before replace (967) | valid prepared `manifest.next` only | discarded (hosts untouched) then fresh transaction commits (A2 holds) | journal still only_next; next run repeats discard → OK |
| 4 | host: after prepare publish (1295) / before `writing` publish | `manifest.json` prepared | recovery restores originals, then fresh transaction commits | next run: `_is_restore_progress` accepts its own prefix writes (A1 holds) |
| 5 | host: during/after in-place claude write (1302) | manifest `writing`; claude target-or-overlay, codex original | classify → restore both originals → commit (A1 holds) | accepted: overlay/prefix states are recognised, run finishes |
| 6 | host: `committed` persisted (1310) | manifest committed, hosts = targets | verify targets → cleanup → done | n/a (no host writes left) |
| 7 | host: inside final `rmtree` after unlink (1120) | empty journal dir | `registration_journal_invalid` every run (E1b wedge) | same wedge |
| 8 | reg: after `_mkdir_restricted` (1304) | empty keyed dir | treated as no journal; run proceeds (contrast with #1) | n/a |
| 9 | reg: torn/complete `manifest.staging` (1307) | staging fragment/complete, no published | fragment unlinked / complete staging ignored; run proceeds | n/a |
| 10 | reg: staging→next done, next→published pending (1308-1309) | valid `manifest.next` only | only_next recovery: restore previous roles, cleanup, re-register (A3 holds) | idempotent compare-and-fix recovery re-runs safely |
| 11 | reg: after add CLAUDE (1574) | published prepared; CLAUDE added, CODEX not | recovery restores previous, cleanup, fresh run commits (A3 holds) | second kill mid-restore → next run compares again, finishes |
| 12 | reg: host timeouts killed (1586) | reg prepared + host journal mid-state | host journal recovered first (1518), roles restored, fresh run commits | host recovery kill → next run resumes restore (A1) |
| 13 | reg: final cleanup rmtree torn (1590) | empty keyed dir | treated as no journal; run proceeds | n/a |
| 14 | lock: holder killed while journal active | lock file remains (byte lock freed by OS), journal active | next run acquires lock, recovers journal (named `registration_busy` only on live contention) | recovery kill → next run resumes |

## NOT VERIFIED
- Claude Code/Codex rewrite mechanics (temp+replace vs in-place): decides how reachable E4's file-id
  change is on a real host; no client was run.
- E6 executed only as far as reading the ps1 seam; no real `claude`/`codex` call was made (out of
  bounds), so the silent-rollback claim rests on `$LASTEXITCODE` semantics.
- `os.replace` atomicity assumed on NTFS (documented MoveFileEx semantics); no power-loss simulation.
- Partial `os.write` inside `_write_private` (torn >0-byte manifest.next) assumed unreachable for one
  syscall on NTFS regular files; only the 0-byte window is demonstrated.
- Round-2 layout: reconstructed from the R9.diff removals (no registration journal existed in g5) and
  the bug046 plan; the exact historical on-disk shape of a "round-2 shared directory" journal was not
  available in this tree, so E5 demonstrates this code's behaviour toward that shape, not the writer.
