# gpt-6.1-sol review, g5r9_journal, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

**F1 — P1 — Recovery applies another instance’s journal to the current provider.**  
Locations: `tools/install_mcp.py:1160`, `:1239`, `:1289`, `:1316`.

The shared journal records previous specs without their registration name. Recovery uses whichever provider the next installer supplies.

Executed scenario with fake providers and in-memory journal storage:

- Alpha’s previous and desired specs carry `("--client", "--supervised")`, following `("-m", "dayz_mcp")`; commands change from `C:\A-old\python.exe` to `C:\A-new\python.exe`.
- Kill alpha at `after_add_CLAUDE`.
- Run beta against the same journal, changing `C:\B-old\python.exe` to `C:\B-new\python.exe`, without `--supervised`.

Wrong result: beta’s registrations become **alpha’s previous specs**, then beta raises `registration_would_drop_options`. Alpha’s Codex registration remains absent, and the journal is deleted.

**Fix:** persist the registration identity and recover through its owning provider; never replay a shared journal through an unrelated instance.

**F2 — P1 — Registration recovery cannot reach host-config recovery after a torn timeout write.**  
Locations: `tools/install_mcp.py:1239`, `:1289`, `:1337`.

Executed scenario using a file-backed fake provider that parses JSON/TOML:

- Register both desired roles and enter `apply_host_timeouts`.
- Claude’s original is 166 bytes; its timeout target is 195 bytes.
- Interrupt the Claude write with current bytes equal to `target[:161] + original[161:]`. The existing classifier recognises this as `own_torn`.
- Run `register_transaction` twice more.

Wrong result: both retries raise `JSONDecodeError` while registration recovery probes Claude. Both journals remain. Host-config recovery, which can repair these bytes, is never reached. A real CLI unable to parse the torn configuration likewise prevents this recovery sequence.

**Fix:** recover the pending host-file transaction before provider probes or mutations, with coordinated ordering between both journals.

**F3 — P1 — A torn first registration-manifest write permanently wedges installers.**  
Locations: `tools/install_mcp.py:1190`, `:1207`, `:1214`.

Executable state: the journal directory contains only `manifest.next` with bytes `b'{"previous":'`; `manifest.json` is absent and provider registrations remain unchanged. This can result from termination during the first `_write_private`, before any provider mutation.

Executed result: two consecutive `register_transaction` calls both raise `RegistrationTransactionError("registration_journal_invalid")`; the journal remains. The default shared root blocks subsequent instance installers.

**Fix:** stage a complete manifest before exposing the active journal, and cover interruption during the first manifest write.

## SPEC COVERAGE

| Requirement | Status | Evidence |
|---|---|---|
| A1: accept finished originals and partial restores | **done** | Exact-original acceptance at `host_config.py:1152`; 29,791 pure classifier cases passed against an independent prefix-write/truncate oracle. |
| A1: 160/189-byte double-crash regression, then two recovery runs | **done** | Reproduced with in-memory file handles; originals restored and journal cleared. |
| A2: valid prepared `manifest.next` recovery | **done** | Publication-crash scenario followed by two successful runs reproduced in memory. |
| A2: prove unchanged files before discard | **done** | Path, identity and original-byte checks precede discard. External-edit scenario preserved bytes and returned conflict. |
| A3: durable role registration plus timeouts | **wrong** | F1–F3 prevent reliable recovery. |
| A3: kill-between-adds regression | **done, narrowly** | Supplied gate covers one dictionary provider and one registration identity; it excludes timeout files. |
| A3: PowerShell rollback and validation seam | **done for the tested failure path** | `-ValidateRegistrationRollback` returned `registration_rollback_ok` with `-ExecutionPolicy Bypass`. |

## GATE GAP

The registration regression uses one instance, passes no `host_configs`, and interrupts only after a completed add. It cannot detect cross-instance journal consumption, malformed host files blocking recovery, or interruption during initial manifest creation. Concurrent installers and rollback-command failures also remain uncovered.

## PREMISE

A1’s two original-preserving runs correctly exercise recovery: `apply_host_timeouts` subsequently reapplies timeout targets.

The supplied diff omits the actual `PROJECT-MAP.md:84` change reported by the implementer. Also, registration uses a separate journal; the changelog’s “uses that journal” wording is inaccurate.

## NOT VERIFIED

The focused suite was attempted locally: four tests could not create temporary directories, and the PowerShell test encountered script policy restrictions. The supplied orchestrator gate was not independently reproduced.

Adversarial reproductions used real transaction/recovery code with in-memory filesystem seams. Native filesystem durability, ACLs, real CLI registrations, actual process termination and sealed-launcher contents were not verified. No files were modified.