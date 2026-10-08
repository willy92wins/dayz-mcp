# AUDIT.md — implementer-grade pass, DayZ-MCP independent instances (g5)

Auditor: external. Prior reports were leads only; every claim was re-derived from the code; executable proofs ran via the project venv (`tools/.venv-mcp`), in-memory/temp only.

## FINDINGS

### C1 — P1 — After a settled migration, a legacy `DayZ_MCP_130`-tree writer is an unserialized second writer
`tools/dayz_mcp/identity_migration.py:1584-1593`:
```python
        settled = _settled_receipt(
            source_path, backup_path, receipt_path,
            pending_receipt_path, marker_path, next_path,
        )
        if settled is not None:
            return settled
```
The settled shortcut returns before `_assert_quiescent`. Proven executably: a second `ensure_runs_v1_backup` call over a settled store invoked `scan_fn` **0** times (first call: 2). Steady-state exclusion is `RootWriterLease` (instance_context.py:190-239, byte 1 of `.daemon-startup.lock`), which only new-code processes take; the legacy tree has neither the lease code nor the `instance_context.py` contract marker (classifier verified: legacy daemon → `blocker` pre-settlement; legacy client → `not_writer`).
Reasoning: the premise is that the legacy `DayZ_MCP_130` tree "may keep running next to the new trees". Before settlement, live legacy writers block migration (fail-closed, verified). After settlement nothing observes them: the gate skips scanning and the lease is uncontended, so a legacy daemon started later (manually, or via a registration still pointing at the legacy tree until a named install re-registers) writes the same `runs.json`/`coordination.json` beside the new daemon. One started between the second `_assert_quiescent` (identity_migration.py:1612-1619) and receipt publication survives into the settled state identically.
Failure scenario: settle the 130 migration; start the legacy daemon (`python -m dayz_mcp --daemon --port 8785` in `C:\temp\DayZ_MCP_130`); start the new `--instance 130` daemon. Both persist to `%LOCALAPPDATA%\DayZ_MCP_130\runs.json` and `coordination.json`; last writer wins; manifest and in-memory coordination diverge silently.

### C2 — P1 — Relative `DAYZ_MCP_SHARED_ROOT` splits the physical-box interlock and build serialization
`tools/dayz_mcp/server_cli.py:87-88`:
```python
    if override:
        root = Path(override)
```
No absoluteness check, while the same change demands absolute paths elsewhere (`validate_game_path` server_cli.py:44-48; `_accredited_shared_root` dayz_test_worker.py:296-302). `box_admission._lock_path` (23-28) and `shared_build_lock_paths` derive from it.
Proven executably: with `DAYZ_MCP_SHARED_ROOT=locks`, instance A (cwd A) held `box_admission()` and instance B (cwd B) was **also admitted**; only `cwdA\locks\box-admission.lock` existed.
Failure scenario: two daemons with different cwds and the same relative override both perform destructive preparation, storage rotation, Steam mutation and spawn — the F05 race the interlock was added to close. The same value sent to the sealed worker fails `request_integrity_failed`, so builds refuse (visible) while box admission silently splits (invisible).

### C3 — P2 — `record_daemon_event` references a nonexistent `config`; audit events are silently dropped
`tools/dayz_mcp/daemon.py:1424-1437`:
```python
    def _write() -> bool:
        try:
            sink = writer
            if sink is None:
                if not daemon_generation:
                    return False
                sink = JsonlAuditWriter(
                    RuntimePaths.for_token(getattr(config, "instance_token", None)),
                    daemon_generation,
                )
```
`record_daemon_event` (daemon.py:1402-1409) has no `config` parameter and there is no module global; `except Exception` swallows the `NameError`. Proven executably: called with `daemon_generation="g1"` and no writer it returns `False`. Callers pass `writer=getattr(state, "audit_writer", None)` (daemon.py:1496-1502, 1523-1529), so daemon-start/stopping events fired before coordination activation are lost.

### C4 — P2 — Named exec-audit is split-brained between server and daemon writers
`tools/dayz_mcp/server.py:857-860`:
```python
    def exec_audit_path(self) -> Path:
        if self.config.exec_audit_path is not None:
            return Path(self.config.exec_audit_path)
        return Path(__file__).resolve().parents[1] / "_audit" / "exec_enforce.jsonl"
```
vs daemon.py:343-352, which names `exec_enforce-<token>.jsonl` for a named instance. The server-side auditor (server.py:843-855; entries carry no instance field) appends to the shared `exec_enforce.jsonl` for a named instance while its daemon appends to the token file. Scenario: instances `130` and default with exec-enforce on — both servers' verdicts interleave in one file; `exec_enforce-130.jsonl` misses the server half.

### C5 — P2 — Doctor cannot select a named instance; two entries accept selector forms the canonical scanner refuses
`tools/dayz_mcp/doctor.py:1426-1437`: the CLI parser declares only `--daemon-policy/--json/--require-clean`; `default_sources` always resolves `registration_name(current_instance_token())` with a default binding (doctor.py:277-301), so the doctor inspects only `dayz-mcp` and the `DayZ_MCP` root; named instances are unverifiable. Proven executably: `validate_entry_selector(["--inst","130"])` is accepted; the knowledge-pack parser accepts `--instance=130` and `--inst 130` (knowledge_pack.py:313,324-341); `stdio_bridge.main` (stdio_bridge.py:418-441) forwards glued/abbreviated tokens unvalidated. A glued form refused by every direct server entry succeeds via the bridge probe — selection rules differ by entry point.

### C6 — P2 — `install-mcp.ps1` registration is not transactional; the canonical installer is
`tools/install-mcp.ps1:884-898`:
```powershell
  if ($ReplaceExistingRegistration) {
    ... & $CodexCmd mcp remove $ServerName ...
  }
  ... & $CodexCmd mcp add $ServerName -- $VenvPython @codexArgs ...
  if ($LASTEXITCODE -ne 0) {
    throw "codex mcp add dayz-mcp failed (exit $LASTEXITCODE). Stopped without a Codex remove."
  }
```
No rollback of the Claude-side add/remove when the Codex side fails. Scenario: `-Register -ReplaceExistingRegistration` with a failing `codex.cmd mcp add`: the old registration was removed from both hosts and re-added only to Claude; `resolve_daemon_provenance` then raises `daemon_provenance_incomplete`, dead for both clients until a repair run. The Python path rolls back (install_mcp.py:1169-1213); this alternative entry does not.

### C7 — P2 — Capture token resolution converts every error into the default instance
`tools/mcp_capture.py:550-557`:
```python
def _bound_capture_token() -> str | None:
    """Validated in-process selector. Omission keeps the historical paths."""
    try:
        from dayz_mcp.server_cli import current_instance_token, validate_instance_token
        return validate_instance_token(current_instance_token())
    except Exception:
        return None
```
Proven executably: with a malformed selector in argv, `frame_state_path()` returned `...\DayZ_MCP\capture-frame-state.json` (default root) instead of failing; `resolve_capture_dir` (mcp_capture.py:1466-1473) falls back identically. A named capture consumer that cannot establish its binding writes the default sidecar; two instances then share one frame-state file and the frame fence corrupts across instances.

### C8 — P3 — Programmatic-activation lease is never released and its owner identity is `id()`-keyed
`tools/dayz_mcp/daemon.py:398-401,412-414`:
```python
        lease = RootWriterLease(paths.root, owner=state)
        if not lease.try_acquire():
            raise RuntimeError("state_root_writer_busy")
        state.root_writer_lease = lease
```
released only in the `except BaseException` arm; owners matched at instance_context.py:188:
```python
        self._owner_id = id(self if owner is None else owner)
```
Scenario: an embedding process activates coordination twice for one root — the second activation is refused (`state_root_writer_busy`) until process death, or, if the first owner was garbage-collected and its id recycled, an unrelated second owner is admitted. Production `run_daemon` (daemon.py:1550-1557) releases correctly.

### C9 — P3 — Journal crash before first manifest publication blocks all future registrations, with no replay
`tools/dayz_mcp/host_config.py:965-967`:
```python
    temporary = journal / "manifest.next"
    _write_private(temporary, payload)
    os.replace(temporary, _manifest_path(journal))
```
host_config.py:963-967 writes `manifest.next` then `os.replace`s it; `_load_manifest` (993-998) reads only `manifest.json` and raises `registration_journal_invalid` on absence; `_recover_if_needed` (1141-1150) propagates. Death between the two steps leaves a recoverable `manifest.next` that is never replayed; every later `apply_host_timeouts` (both instances — the journal is shared, host_config.py:911-915) fails until an operator deletes the journal directory. Fail-closed; availability only.

### C10 — P3 — Build-lock wait capped at ~10s; orphan `.partial.<pid>` files are never swept
`tools/dayz_mcp/server_cli.py:124-125`:
```python
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
```
and `tools/build_native_launcher.py:274`:
```python
    temporary = cache.with_name(cache.name + f".partial.{os.getpid()}")
```
Scenario: a builder holding the lock longer than ~10 s (LK_LOCK retries 1 s apart, 10 times) makes the second instance's serialized build fail with `OSError`; a killed builder leaves its `.partial.<pid>` file forever (the `finally` releases only the lock).

## TRACE TABLE (writer -> readers -> cleanup)

| State | Writer(s) | Reader(s) | Cleanup / replay | Status |
|---|---|---|---|---|
| runs.json, coordination.json, audit dir | daemon (`RuntimePaths.for_token`, daemon.py:461,1549), build_server_state:398 | daemon, migration gate, doctor:301, p0s_gate:352-357 | root lease released on daemon exit (1554-1557) | C1 (legacy writer post-settlement), C3 (audit events) |
| Root-writer lease (byte 1, `.daemon-startup.lock`) | run_daemon:1550, build_server_state:398 | second writer's try_acquire | finally-release in run_daemon; exception-only in build_server_state | C8 |
| box-admission.lock | `_start_run_reserved` (process_lifecycle.py:3556-3566) | contending instance start (409) | context exit / process death | C2 |
| Build locks + `.partial` | sealed worker (dayz_test_worker.py:799-818) | contending builder | finally unlock; partials unswept | C2 (root), C10 |
| Host-config journal + Claude/Codex configs | `apply_host_timeouts` via both installers (install_mcp.py:1202-1207; ps1:875-898) | `resolve_daemon_provenance` (server.py:925-941) | shared `_recover_if_needed` | C6 (ps1), C9 |
| Migration marker/receipt/backup | `ensure_runs_v1_backup` under gate lock (identity_migration.py:1575-1661) | next daemon start, p0s gate | `_recover_backup_transaction`; settled shortcut | C1 |
| Exec audit | server (server.py:843-860) + daemon (daemon.py:343-366) | review tooling | rotation only | C4 |
| Capture sidecar / captures dir | capture tools (mcp_capture.py:560-573,1466-1473) | capture readers | none needed | C7 |
| Inbox feedback.jsonl | server `append_feedback`/`_append_jsonl` (inbox.py:261-292) | server readers | lock released by handle | OK (per-token root) |
| knowledge.json / knowledge-pack | server, pack CLI (knowledge.py:453-460, knowledge_pack.py:45-60) | server, pack CLI | unsync by manifest; named cannot own global skills (knowledge_pack.py:194-201) | OK |
| Registrations (Claude/Codex) | installers | server provenance (token/port/game_path compared, server.py:934-939; name bound to token, host_config.py:315-318) | rollback (py) / none (ps1) | C6 |
| Profiles `dayz_mcp.json` | worker `_start_core` (`profiles-<token>`, dayz_test_worker.py:316-322) | game; daemon binding check (loopback.py:1728-1745); endpoint recheck (process_lifecycle.py:2175-2195) | n/a | OK (default-into-named refused by port mismatch) |

## NOT VERIFIED
- Sealed launcher rebuild/reseal artifacts: `approved-launchers.lock` is a new **empty** file; no built launcher, policy JSON or closure evidence is here, so SPEC item 7 (F09 alignment, unchanged default bundle) is unverifiable.
- No gate/regression suite executed; WMI capture, live psutil classification, Steam re-entry under `box_admission`, and the `claude`/`codex` probe fixtures were read only.
- Policy revalidation closures read, not driven through a live config replacement.
- Production cwd of the WMI-spawned daemon (affects C2's trigger) not observed live.
- `server.py:7430` in-process daemon spawn path read, not executed.
