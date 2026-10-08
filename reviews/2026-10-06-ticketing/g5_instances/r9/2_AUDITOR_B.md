# AUDIT.md — batch g5 data audit B: exclusion, concurrency, selection

Auditor angle: lifetime lease, shared-box admission, build locks, migration vs live writers, and
selector validation at every entry point. Method: code reading, grep, and in-memory/temp-dir
executions with the project venv. No installer, daemon, registration or DayZ process was run.

## FINDINGS

**B1 — P2 — A relative `DAYZ_MCP_SHARED_ROOT` gives every instance its own lock root, so box
admission and build locks stop being shared.**
```python
# tools/dayz_mcp/server_cli.py:87-88
    if override:
        root = Path(override)
# tools/dayz_mcp/box_admission.py:26-28
    root = shared_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / "box-admission.lock"
```
`Path(override)` is never resolved or made absolute. A relative value is resolved against each
process cwd at `mkdir`/`os.open` time. Executed with the project venv: with
`DAYZ_MCP_SHARED_ROOT=relative-locks`, cwd `%TEMP%` resolves the admission lock to
`C:\Users\...\Temp\relative-locks\box-admission.lock` and cwd `C:\` to `c:\relative-locks\...`
— two distinct lock files. Two independent trees (the deployment premise: different tools trees,
different cwd) therefore do not exclude each other: both `box_admission()` acquisitions succeed,
both daemons reach destructive preparation and spawn (`process_lifecycle.py:3558`), and
`shared_build_lock` (server_cli.py:112 `base = ... / "build-locks"`, worker root
`dayz_test_worker.py:806-807`) splits the same way. This is the two-launches-one-box race F05
was meant to close, reintroduced through one unparsed env value. Default (no override) is
unaffected; severity reflects the precondition, not the consequence.

**B2 — P2 — The build lock is a ~10-second bounded wait, so it does not serialize real builds.**
```python
# tools/dayz_mcp/server_cli.py:119-125
def _lock_byte(descriptor: int) -> None:
    import msvcrt
    ...
    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
# tools/dayz_mcp/dayz_test_worker.py:806-807
    lock_root = _accredited_shared_root(payload)
    with shared_build_lock(target, temp, root=lock_root):
```
Measured with the project venv: a holder keeping the byte for 13 s makes a contender's
`LK_LOCK` raise `OSError` (errno 36) after 9.1 s. Addon Builder runs take minutes, so a second
instance (or second client) building the same `@Mod\Addons` target or temp dir does not queue
behind the first — the `with` at `dayz_test_worker.py:807` raises a raw OSError (no
`build_busy` code; the surrounding `execute_dayz_test_worker` has no handler there), the worker
fails, and the client sees a generic failure. Fail-closed (never two writers on the target), but
spec item 6's "serialize shared build targets/temp paths independently" degrades to
"serialize-or-die-in-10-s", and the loser's error names nothing actionable.

**B3 — P2 — After the settled receipt, the writer scan never runs again, on a justification the
new classifier invalidated.**
```python
# tools/dayz_mcp/identity_migration.py:1584-1593
        settled = _settled_receipt(
            source_path, backup_path, receipt_path,
            pending_receipt_path, marker_path, next_path,
        )
        if settled is not None:
            return settled
```
The comment above it (:1576-1583) says demanding an empty machine "buys no safety ... the scan
counts every `-m dayz_mcp` process of every open session as a blocker (:739-743)". That was true
of the old scan; `_migration_disposition` (:765-810) now classifies role first, then root, and
returns `not_writer` for valid clients and accredited different-root writers, so blockers are
only same-root or unestablished writers — exactly what a startup scan should wait for. The
consequence of the shortcut: once the receipt is settled, no daemon start ever re-checks for
writers. The RootWriterLease excludes supported writers, but the patched legacy tree of F03
(hardcoded `DayZ_MCP_130`, no lease) started after settlement writes the same `runs.json` the
lease-holding daemon checkpoints for its whole lifetime (`process_lifecycle.py:1660-1670`). The
"nothing left to write" claim is false for the daemon's own future writes. Executable scenario:
migrate `130` to a settled receipt; start the fixture copy's daemon on 8785; both daemons
activate coordination over `%LOCALAPPDATA%\DayZ_MCP_130\runs.json`; one overwrites the other's
manifest.

**B4 — P3 — `install-mcp.ps1` does not reject a duplicate `-Instance`.**
```powershell
# tools/install-mcp.ps1:54-58
foreach ($extra in @($args)) {
  $text = [string]$extra
  if ($text -match '(?i)instance' ...) { Exit-DayZMcpSelector "unconsumed_selector_argument" }
```
A repeated `-Instance 130 -Instance 131` binds to the parameter (last wins), never reaches
`$args`, and passes the token grammar at :71-80. The canonical scanner refuses duplicates
(`server_cli.py:431-436`, `duplicate_instance_flag`), and the comment at :52 claims "Same rules
as dayz_mcp.server_cli.validate_entry_selector". Result: `dayz-mcp-131` installed where a reader
of the command line expects `130`. Not executed (installer boundary); deduced from PowerShell
parameter binding. Glued forms are covered: `-Instance=...` errors or lands in `$args` and is
rejected by the scan.

## STATE TABLE

| Resource | Lock / state | Scope | Acquire → hold → release |
|---|---|---|---|
| State-root writer | `RootWriterLease`, byte 1 of `<root>\.daemon-startup.lock` (`instance_context.py:176-263`) | per state root, cross-process, one owner object per process | `run_daemon` acquires before election/migration/bind (daemon.py:1550-1553); released in `finally` (:1556-1557); programmatic activation releases on exception only (:412-415); OS releases on death; same-process second owner refused (:195-199) |
| Daemon startup election | byte 0 of same file (`identity_migration.py:347-398`) | per root | acquired inside `_run_daemon_body`; non-blocking; loser re-probes health then exits contended |
| Migration transaction | `.runs-v1.lock` in `<root>\migration\P0S-IDENTITY-V2` (:414-464) | per root | whole backup transaction; two quiescence scans inside (:1594, :1658); drift check before receipt publish (:1668-1677) |
| Physical box | `box-admission.lock` under shared root (`box_admission.py`) | cross-instance, cross-process, thread-shared | held across steam prepare, profile prep, storage rotation, spawn and settlement (`process_lifecycle.py:3558-3567`); not admitted → 409 `box_admission_busy`; thread re-entrant; death releases |
| Build targets/temp | `build-locks\<sha256>.lock` under shared root, sorted acquisition (`server_cli.py:107-181`) | per normalized resource | held during ADDON_BUILDER (`dayz_test_worker.py:807`); bounded 10 s wait (B2) |
| Steam mutation | `SteamPreparationGate` thread lock (`steam_prepare_supervisor.py:133`) | process-local | covered cross-instance only by box admission; helper spawned and drained inside it |
| Frame-state sidecar | `O_EXCL` sibling lock, 0.5 s timeout, 30 s stale-break (`mcp_capture.py:576-609`) | per sidecar file | per capture call |
| Host-config pair | shared journal `%LOCALAPPDATA%\DayZ_MCP\host-config-transaction` (`host_config.py:911-915`) | shared by all instances | recover-before-write (:1227-1232); installer registration mutations run OUTSIDE it (M6) |

## MECHANICAL CHECK

- M1 CONFIRMED — `server_cli.py:87-88` keeps the override relative; executed: two cwds resolve two `box-admission.lock` files (extended in B1 to build locks).
- M2 CONFIRMED — `knowledge_pack.py:309-316` plain parser (`--inst`, `--instance=`, duplicates accepted), validated only at :336-339; `stdio_bridge.py:418-419,432-443` forwards unvalidated tokens into `official_client_argv` (:88-91).
- M3 CONFIRMED — `server.py:7325-7330` binds with `replace=True`; grep: `reject_conflicting_environment` called only at daemon.py:1545, install_mcp.py:1301, p0s_gate.py:347, secure_launcher.py:289/299; client/embedded never.
- M4 CONFIRMED — `doctor.py:1427-1437` parser has no `--instance`/`--game-path`; `current_instance_token()` (:282, :301) can only resolve omission.
- M5 CONFIRMED — `mcp_capture.py:550-557` swallows every selection error to `None`; :570-573 then derives the default sidecar root.
- M6 CONFIRMED — `install_mcp.py:1191-1207` mutates registrations then `apply_host_timeouts` at :1204, no journal begin; `install-mcp.ps1:876-898` adds Claude then `throw`s on Codex failure (:897) with no rollback.
- M7 CONFIRMED — executed `record_daemon_event` without writer: `daemon.py:1431` references out-of-scope `config`, NameError swallowed at :1435-1437, returns False.
- M8 CONFIRMED — `daemon.py:347-351` names `exec_enforce-<token>.jsonl`; `server.py:856-859` (embedded) always `exec_enforce.jsonl`.
- M9 CONFIRMED — `build_native_launcher.py:274-276` per-PID partial, :288-290 removes only on integrity failure, :293-301 finally releases only the lock; stale `.partial.<dead-pid>` files are never reclaimed.
- M10 CONFIRMED — `host_config.py:963-967` `manifest.next` → `os.replace`; :993-998 `_load_manifest` raises `registration_journal_invalid` when `manifest.json` is absent; grep shows no replay of `manifest.next`.
- M11 CONFIRMED — `daemon.py:393-415`: activation-path lease released only on exception; success leaves the descriptor in `_lease_fds` until explicit release/process exit (production path is lease-per-`run_daemon`, released in `finally`).

## NOT VERIFIED

- Live cross-process behavior of `RootWriterLease`, `box_admission`, and the startup election: no daemon or DayZ process may be started; verified by code reading, venv lock-semantics probes, and the existing test suite's claims only.
- `install-mcp.ps1 -ValidateOnly` (B4) and any PowerShell binding: not executed.
- The sealed `p0s_daemon_bootstrap.py` and the sealed worker bundle are not in this tree; their lease/admission conduct inside a deployed bundle is unverifiable here (classifier references only, identity_migration.py:574, :787).
- `wmi_host.py` spawn execution (env inheritance of a conflicting `DAYZ_MCP_INSTANCE` into the spawned daemon) — read, not run.
- Whether any deployment sets `DAYZ_MCP_SHARED_ROOT` (relative or not) in practice; B1 requires it.
- `pytest`-style invocations against `_invocation_is_test()` (server_cli.py:66-73 detects only `unittest`/`DAYZ_MCP_FAST_TESTS`); a non-unittest test runner would use the real shared root.
