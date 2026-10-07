# DZ-R9 step 6 loop-back for batch g5 (one round)

Written by the orchestrator from the step-6 re-audit of g5's composed fixes: a gpt-6.1-sol mechanical re-check
(N1-N7) and two GLM angle auditors (D exclusion, E persistence), each finding verified by the orchestrator
against the code. The workspace is g5's approved tree plus the three DZ-R9 fix batches (journal, locks,
selection), composed and gated. Fix exactly the item below; keep everything else. Never run an installer, pip,
a registration, `claude`/`codex mcp`, a daemon, DayZ or an MCP tool, from this tree or from any copy; tests use
fake providers and temporary roots only.

## ITEM g5-r9-step6

Shared host-config journal (`tools/dayz_mcp/host_config.py`): two crash states it produces itself wedge every
later installer of every instance with `registration_journal_invalid` until someone deletes
`%LOCALAPPDATA%\DayZ_MCP\host-config-transaction` by hand. The registration journal in `tools/install_mcp.py`
already survives both states; make the host-config journal match it.

- E2 / N5 (P1, `host_config.py:963-967`, `:1005-1017`): a kill inside the FIRST publication, after
  `_write_private` truncated or created `manifest.next` and before its bytes landed, leaves a 0-byte or partial
  `manifest.next` and no `manifest.json`. Publish through a staging name first (write `manifest.staging`, then
  `os.replace` to `manifest.next`, then to `manifest.json`), the way `_write_registration_manifest`
  (`install_mcp.py`) does, and discard a staging fragment on the next run: it never became a manifest and
  nothing was written to a host file before the first publication completed. Keep A2's handling of a complete
  `manifest.next`.
- E1 (P1, `host_config.py:1174-1176`, `:1291-1295`, `:1118-1120`): an existing journal directory that holds
  neither `manifest.json` nor `manifest.next` (a kill after `_mkdir_restricted(journal)` and before the first
  publication, or inside `_cleanup_journal`'s `rmtree` after the manifest was unlinked) is a journal that
  recorded nothing: remove it and continue, as `_load_registration_manifest` does for the registration journal.
  Any other unexpected content still refuses.
- N4 (P3, `tools/dayz_mcp/server_cli.py:106-108`, `:164`, `:199-202`; `tools/dayz_mcp/bridge_errors.py:219-229`):
  `relative_shared_root` and `invalid_build_lock_wait` are raised as bare `OSError`, so `dayz_test_run` reports
  `dayz_test_failed:OSError` and the code never reaches the caller. Raise them through a dedicated exception
  type that carries an identifier-shaped `code`, and let `_opaque_dayz_test_failure` pass that `code` the same
  way it does for `NativeLauncherBackendError` (only identifier-shaped tokens cross the wire, never a path).
  Same for `registration_lock_open_failed` in the installer's error mapping (`install_mcp.py`, the branch that
  falls back to `installer_failed`).
- N6 (P3, `tools/install_mcp.py:1246-1248`): `_acquire_registration_lock` accepts NaN and infinity as the wait
  (the deadline never passes). Accept only a finite number >= 0; anything else is
  `invalid_registration_lock_timeout`.
- D1 (P3, `tools/install_mcp.py:1204-1207`, `:1249`, `:1474`): `register_transaction(journal_root=...)` accepts
  a relative root, so the installer lock and the journal resolve against the process cwd and two callers in
  different directories take different locks. Refuse a relative `journal_root` (and `host_journal_root`) with a
  named error before any lock, journal or provider call, as A7 does for `DAYZ_MCP_SHARED_ROOT`.
- Regressions, each must FAIL on this tree: (1) a 0-byte and a partial `manifest.next` with no `manifest.json`:
  the next two runs succeed, the host files equal the run's targets and the journal is gone; (2) the empty
  journal directory from both kill points: same; (3) a directory holding an unexpected file still refuses;
  (4) `dayz_test_run` with a relative `DAYZ_MCP_SHARED_ROOT` reports a failure whose text names
  `relative_shared_root`; (5) NaN and infinity lock waits are refused; (6) a relative `journal_root` is refused with no provider call.

Do not change the PowerShell installer, the registration journal's protocol, the lock files or anything
outside these items. `tools/dayz_mcp/host_config.py` and `tools/dayz_mcp/server_cli.py` are sealed modules:
regenerate `tools/packaged-modules.lock.json` with `python tools/write_packaged_modules_lock.py` after the
change (the release reseals the launcher).
