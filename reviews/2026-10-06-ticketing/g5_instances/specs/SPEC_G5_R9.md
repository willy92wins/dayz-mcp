# DZ-R9 fixes for batch g5 (independent instances)

Written by the orchestrator from the DZ-R9 audit of g5: a mechanical census, two GLM angle auditors, an
independent gpt-6.1-sol verifier on every claim, an orchestrator self-sample and an orchestrator experiment.
The workspace is g5's approved final tree. Fix exactly the item of your batch; keep everything else.
Never run an installer, pip, a registration, a daemon, DayZ or an MCP tool, from this tree or from a copy.

## ITEM g5-r9-journal

Host-config transaction journal and installer registration (data-critical: a wedge blocks every instance's
installer until someone deletes the shared journal by hand).
- A1 (`tools/dayz_mcp/host_config.py:1102-1119`, `:1183-1189`): when recovery resumes a journal in the
  restoring-original phase, a role whose restore already finished is rejected as `registration_recovery_conflict`
  when its original is shorter than the recorded torn state. Recovery must recognise every byte-exact state it
  can have produced itself (original fully restored, or a prefix of the restore it was writing) and finish the
  restore; only content it cannot have produced is a conflict. Regression: the auditor's executed scenario
  (claude original 160 B, target 189 B; crash after the claude write; recovery crashes after rewriting the
  original; the next two runs must finish and clear the journal, both files equal to their originals).
- A2 (`host_config.py:963-968`, `:993-1004`): a journal whose `manifest.json` is absent but whose
  `manifest.next` holds a valid prepared manifest (crash during the first publication, nothing written to any
  host file yet) is replayed or discarded deterministically, never left as `registration_journal_invalid`
  forever. Prove "nothing written" from the manifest and the host files before discarding. Regression: a crash
  injected in that `os.replace`, then two fresh runs succeed.
- A3 (`tools/install_mcp.py:1185-1210`; `tools/install-mcp.ps1:876-898`): the registration of the roles
  (Claude and Codex add/remove, then the timeouts) runs inside the same durable journal discipline as the
  host-config transaction (begin before the first mutation, recovery on the next run), so a kill between the
  two `provider.add` calls is repaired by the next installer run. The PowerShell path, on a failed Codex add,
  rolls back the Claude registration it added before throwing. Regressions: a kill between the two adds is
  recovered by the next run (in process, temp journal root, fake providers); the PowerShell rollback is
  exercised through the `-ValidateOnly`-style test seam the batch adds, never by a real registration.

## ITEM g5-r9-locks

Exclusion and locks.
- A6 (`tools/dayz_mcp/instance_context.py:155-159`, `:188`, `:209`): the root-writer lock file is opened
  without FILE_SHARE_DELETE (measured on this host: with it, deleting the held file and creating a new one
  gives two holders). Check every other lock file the change opens (box admission, build locks, migration
  lock, election byte) and apply the same rule. Tests that remove temporary roots release their leases first.
  Owner identity: an explicit owner token object kept alive by the holder, not a bare `id()` that can be
  reused after collection. Regression: in a temp dir, deleting the held lock file fails (or the second
  process cannot lock), for each lock kind.
- A7 (`tools/dayz_mcp/server_cli.py:83-88`, `tools/dayz_mcp/box_admission.py:23-28`): a relative
  `DAYZ_MCP_SHARED_ROOT` is refused (fail closed with a named error), never resolved against a cwd.
- B2 (`server_cli.py:107-125`, `:165-169`; `tools/dayz_mcp/dayz_test_worker.py:806-807`): the shared build lock
  waits for the holder for a bounded, configurable time well above a real build (minutes), polling with
  `LK_NBLCK`, and on timeout the worker answers a named `build_busy` error (not `internal_failure`).
  `dayz_test_worker.py` is a sealed module: regenerate `tools/packaged-modules.lock.json` with
  `python tools/write_packaged_modules_lock.py`. Regression: a holder keeps the lock 15 s, the contender waits
  and then builds; with a short configured limit the contender gets `build_busy`.
- B3 (`tools/dayz_mcp/identity_migration.py:1576-1601`): after a settled receipt, every daemon start still
  scans for live same-root writers with the current classifier (valid clients and accredited other-root
  writers are not blockers) and refuses to start while one exists. Regression: a settled receipt plus a fake
  live process whose argv is a legacy writer of the same root blocks the start; with only clients it starts.

## ITEM g5-r9-selection

Selection consistency and audit naming.
- M2 (`tools/dayz_mcp/knowledge_pack.py:309-339`, `tools/dayz_mcp/stdio_bridge.py:418-443`): both use the
  shared entry selector validation (no abbreviations, no glued or duplicate forms, the server token grammar)
  before any effect.
- M3 (`tools/dayz_mcp/server.py:7325-7330`): the server, client and embedded paths reject a conflicting
  environment selector exactly like the daemon (`reject_conflicting_environment`, daemon.py:1545).
- M4 (`tools/dayz_mcp/doctor.py:1427-1437`): the doctor accepts `--instance` and `--game-path` with the shared
  validation and diagnoses the selected instance.
- M5 (`tools/mcp_capture.py:550-573`): a selection error never falls back to the default sidecar: it is
  reported (the capture result names it) and nothing is written.
- A4 (`tools/dayz_mcp/daemon.py:1430-1436`): the fallback audit writer uses the daemon's actual instance (the
  undefined `config` name is a NameError today); regression: the fallback row is written.
- A5 (`daemon.py:343-352`, `server.py:858-859`): one exec-enforce audit file name per instance, the same from
  both writers.
- One regression per bullet; each must fail on this tree.
