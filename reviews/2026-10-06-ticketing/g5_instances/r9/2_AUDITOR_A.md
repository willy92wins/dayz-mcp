# Data audit A — DayZ-MCP independent instances (batch g5): persistence, migration, recovery

## FINDINGS

**A1 — P2 — Recovery of an interrupted host-config restore refuses its own completed restore and wedges the shared journal forever.**
`tools/dayz_mcp/host_config.py:1102-1119`:
```python
    if len(current) == len(source):
        ...
        return suffix_start <= prefix
    return (
        len(desired) > len(source)
        and len(source) < len(current) <= len(desired)
        and current == desired[: len(current)]
    )
```
with `tools/dayz_mcp/host_config.py:1183-1189`:
```python
            if any(
                not _is_restore_progress(
                    currents[role], decoded[role][2], sources[role]
                )
                for role in decoded
            ):
                raise HostConfigError("registration_recovery_conflict")
```
During `restoring_original`, `desired` is the original and `source` the recorded torn state. A role whose restore already finished is only accepted when its original is not shorter than the recorded state; a shorter original satisfies neither branch, so the code calls its own completed restore "external" and raises. The journal is shared (`_default_journal_root` = `%LOCALAPPDATA%\DayZ_MCP\host-config-transaction`), so every future `apply_host_timeouts` of every instance fails until the journal is deleted by hand; nothing replays or clears it.
Executed scenario (project venv, temp dirs): claude original 160 B, target 189 B. Run 1 crashes at fault `after_write_claude`; run 2 recovery rewrites claude=original then crashes at `after_recovery_write_claude`; run 3 and run 4 both raise `registration_recovery_conflict` while both files already equal their originals (verified True/True).

**A2 — P2 — A journal killed during its first manifest publication is permanently invalid; the safe `manifest.next` is never replayed (M10).**
`tools/dayz_mcp/host_config.py:993-1004`: `_load_manifest` reads only `manifest.json` (`_manifest_path(journal).read_bytes()`), and `tools/dayz_mcp/host_config.py:963-968` publishes via `_write_private(manifest.next)` + `os.replace`. A kill between the two leaves `journal/manifest.next` with a `prepared` manifest and zero config mutations. Every later run raises `registration_journal_invalid`; deleting the journal would be exactly correct (nothing was written) but no code does it. Executed: crash injected in `os.replace` → journal holds only `manifest.next`; two fresh runs both fail `registration_journal_invalid`. Shared journal → all instances' installers blocked.

**A3 — P2 — Installer registration mutations have no durable journal; a kill or the PowerShell throw path leaves hosts half-registered (M6).**
`tools/install_mcp.py:1197`: `provider.add(role, desired[role])`; `:1204`: `apply_host_timeouts(...)`. `previous`/`touched` (:1185-1191) are memory only; `_rollback_registrations` runs solely on in-process exceptions (:1210). `tools/install-mcp.ps1:892-897`: the Codex add is followed only by `throw "codex mcp add dayz-mcp failed (exit $LASTEXITCODE). Stopped without a Codex remove."` — Claude keeps the new registration, Codex the old/absent one; `resolve_daemon_provenance` then reports `daemon_provenance_conflict` until a re-run. Kill -9 between the two `provider.add` calls leaves the same split with no recovery path.

**A4 — P3 — The daemon audit fallback references an undefined name, so fallback audit rows are silently dropped (M7).**
`tools/dayz_mcp/daemon.py:1430-1436`:
```python
                sink = JsonlAuditWriter(
                    RuntimePaths.for_token(getattr(config, "instance_token", None)),
                    daemon_generation,
                )
```
`config` is neither parameter nor closure of `record_daemon_event` and has no module binding (grep-verified); the NameError is swallowed by the `except Exception` at :1440 → `return False`. Reachable whenever `writer is None` and `state.audit_writer` was never set (activation failed before daemon.py:688).

**A5 — P3 — The same named exec-enforce audit stream gets two different filenames depending on which process writes it (M8).**
`tools/dayz_mcp/daemon.py:343-352` returns `audit_dir / f"exec_enforce-{token}.jsonl"` for a named daemon; `tools/dayz_mcp/server.py:858-859` returns fixed `... / "_audit" / "exec_enforce.jsonl"`. A named instance's server-side `audit_exec` rows land in the default audit file, splitting one audit stream across two names.

**A6 — P3 — The lifetime writer lease is defeatable by deleting its lock file, and owner identity is a recyclable `id()`.**
`tools/dayz_mcp/instance_context.py:155-159`: CreateFileW share mask `0x1 | 0x2 | 0x4` (read+write+DELETE) on `.daemon-startup.lock`; `:209` comment: "# FILE_SHARE_DELETE lets a test remove the temp root while the handle, and therefore the lock, is still open."; `:188`: `self._owner_id = id(self if owner is None else owner)`. Deleting the lock file while held lets the next writer create a fresh inode and acquire byte 1: two writers on one root. Same-user action only, and the comment shows a deliberate test affordance, but nothing restricts it to tests; `id()` reuse after owner GC would likewise admit a stranger. Scenario: delete `%LOCALAPPDATA%\DayZ_MCP_130\.daemon-startup.lock` while daemon A serves → daemon B `--instance 130 --port 8785` activates coordination over the same `runs.json`.

**A7 — P2 — A relative `DAYZ_MCP_SHARED_ROOT` silently splits the box-admission and build-lock namespaces per cwd (M1; severity reduced from P1 because only tests set the variable today).**
`tools/dayz_mcp/server_cli.py:83-88`: `override = os.environ.get("DAYZ_MCP_SHARED_ROOT", "").strip()` … `root = Path(override)` — no absolute check; `tools/dayz_mcp/box_admission.py:27-28`: `root.mkdir(parents=True, exist_ok=True)` / `return root / "box-admission.lock"` resolve the relative path against each process's cwd. Verified in memory: `shared_root()` returned the relative path unchanged from two different cwds. Two trees with different cwds then take different lock files; `box_admission()` yields True to both daemons, which prepare profiles, rotate storage and spawn concurrently — exactly the F05 race the lock exists to close. `shared_build_lock` is defeated the same way.

## CRASH MATRIX

Migration artifacts under `<root>\migration\P0S-IDENTITY-V2\`; windows 2, 3, 4, 6, 7, 8, 9 executed with the venv in temp roots (scan/listener stubbed):
1. `runs-v1.lock` acquired → death: OS drops the lock; retry clean.
2. `transaction.next` written → death: marker-less recovery rebuilds the initial marker from the current source and unlinks `next` only as an owned prefix; source drift → permanent `runs_backup_recovery_conflict`. Fail-closed per SPEC. Verified.
3. marker renamed (prepared) → death: recovery deletes backup/pending/marker and redoes. Verified.
4. backup written → death: prefix-checked, deleted, redone. Verified.
5. second quiescence/drift: source modified in-window → `runs_source_or_backup_drift`; next run `_source_matches_transaction` fails → permanent conflict. Fail-closed per SPEC.
6. pending receipt written → death: all artifacts cleaned, redone. Verified.
7. receipt renamed, marker remains → death: recovery unlinks marker and returns the receipt, then re-quiesces. Verified.
8. settled receipt → shortcut returns without scanning. Verified.
9. receipt deleted by hand (F08 case): `incomplete_runs_backup_artifacts`; startup stays blocked. Required fail-closed by SPEC. Verified.
Every wedge ends in `RunsBackupGateError` from the migration, which `_ensure_identity_migration_after_candidate_drain` does not absorb (only TimeoutError/DaemonPythonNotApproved) → the daemon refuses to start until manual cleanup; no window corrupts `runs.json`. The new quiescence classifier was exercised on 11 argv shapes (legacy no-flag writer, `-I` writer, bootstrap script, unknown flag, clients, other tokens, embedded, malformed tail): every case blocks or skips exactly as SPEC §4 requires.

Root-writer lease (byte 1 of `.daemon-startup.lock`; election byte 0 — ranges do not overlap; both size-priming writes are zero-fill and idempotent): acquired before election and migration; death frees the lock; every exit path releases (daemon.py:413, :1557); crash after acquire but before activation re-acquires cleanly. Cross-owner refusal, same-owner re-entry and release verified in-process; cross-process contention not executed (see NOT VERIFIED). Defeats: A6.

Box admission / build locks: death frees the OS lock; a killed builder leaves `.partial.<pid>` garbage only — publish stays `os.replace`-atomic (M9). A7 splits the namespace. The build-lock byte has no fsync; ownership is the lock, not content.

Registration transaction: probe → remove/add → verify → timeouts → re-verify; kill windows leave one host registered (A3); in-process failures roll back and re-verify; rollback failure raises `registration_rollback_failed` (installer re-run repairs).

Host-config journal: prepared→writing→(claude,codex)→committed→cleanup; recovery of prepared/writing/committed/restored verified by reading; restore-path wedges are A1/A2. A concurrent second installer is excluded by the share-0 `_WinFile` handles during recovery and writes; a B that reads originals before A commits fails its drift check before writing anything. Committed+drift and restored+drift refuse fail-closed.

Input bounds of persisted documents: receipt and transaction marker enforce exact key sets, duplicate-key rejection, revision/phase bounds and sha256/size cross-checks against files on disk — malformed or hostile documents fail closed; they are read fully into memory with no size cap (hostile same-user writer is outside the model). Journal manifests/base64 are schema-strict, size uncapped. Selector tokens: grammar `[a-z0-9](?:[a-z0-9-]{0,31})?`, `default` reserved, glued/duplicate rejection at server/installer/gate/secure-launcher/PS; knowledge_pack and stdio_bridge accept looser shapes (M2) but still validate the value. `shared_lock_root`/`--game-path`: absolute, normalized, NUL-free. Frame-state sidecar: 32-window bound; `_bound_capture_token` degrades to the default sidecar on any error (M5).

## MECHANICAL CHECK
- M1 CONFIRMED — verified unresolved `Path(override)`; per-cwd lock dirs (A7; P2 in practice, no production setter).
- M2 CONFIRMED — knowledge_pack parser accepted duplicate/glued/`--inst`/`default`/space forms in-memory; value still token-validated; stdio_bridge.py:418,442 forwards unvalidated.
- M3 CONFIRMED — server.py:7328 binds without `reject_conflicting_environment`; probe: server accepted `DAYZ_MCP_INSTANCE=999` with `--instance 130`; daemon (daemon.py:1545) rejects the same config.
- M4 CONFIRMED — doctor.py:1437 parser declares no `--instance`/`--game-path`; the named doctor is not CLI-selectable and always resolves the default root.
- M5 CONFIRMED — mcp_capture.py:556-557 `except Exception: return None` → :572 default sidecar; reachable in the standalone published module where the `dayz_mcp` import fails.
- M6 CONFIRMED — see A3.
- M7 CONFIRMED — see A4.
- M8 CONFIRMED — see A5.
- M9 CONFIRMED — build_native_launcher.py:274 `.partial.{pid}`; :293 finally frees only the lock; stale partials accumulate (garbage only).
- M10 CONFIRMED — see A2 (executed end-to-end).
- M11 CONFIRMED — daemon.py:401/:413; success-path retention is the designed lifetime exclusion; the descriptor persists in `_lease_fds` until explicit release or process exit (leak only in long-lived non-daemon hosts).

## NOT VERIFIED
- Cross-process byte-range contention for lease/election/box lock on two live OS processes (verified in-process only; Win32 semantics assumed).
- WMI spawn argv shapes, sealed launcher/redirector identities and the launch-ancestor chain on a live machine.
- `claude`/`codex` CLI behavior and the PowerShell installer end-to-end (read-only).
- The real legacy `DayZ_MCP_130` store contents and receipt (boundaries forbid touching it).
- Addon/PBO behavior (outside this angle).
