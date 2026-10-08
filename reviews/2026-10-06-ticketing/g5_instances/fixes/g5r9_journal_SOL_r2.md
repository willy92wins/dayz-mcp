# gpt-6.1-sol review, g5r9_journal, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

### F1 — P2 — Another instance’s journal still blocks subsequent installers

Locations: `tools/install_mcp.py:1156`, `tools/install_mcp.py:1373`.

Previous F1 is only partially fixed: recovery no longer applies alpha’s specs to beta, but it does not recover through alpha’s owning provider either.

**Executed scenario**, using fake providers and in-memory journal storage:

- Shared `journal_root`; names `dayz-mcp-alpha` and `dayz-mcp-beta`.
- Alpha changes both commands from `C:\A-old\python.exe` to `C:\A-new\python.exe`, with arguments `("-m", "dayz_mcp", "--client", "--supervised")`.
- Beta changes `C:\B-old\python.exe` to `C:\B-new\python.exe`, with arguments `("-m", "dayz_mcp", "--client")`.
- Kill alpha at `after_add_CLAUDE`.
- Call beta’s `register_transaction` twice.

**Wrong output:** both beta calls raise `registration_journal_owner_mismatch`; alpha’s Codex registration remains absent and the shared journal remains. Other instances cannot continue until alpha is explicitly rerun.

**Suggested fix:** recover using the recorded owning identity/provider, or isolate journals by registration identity so an interrupted instance cannot block unrelated installers.

### F4 — P1 — Ownership check races with publication and loses another instance’s journal

Locations: `tools/install_mcp.py:1368`, `tools/install_mcp.py:1408`, `tools/install_mcp.py:1202`, `tools/install_mcp.py:1451`.

Loading/checking ownership, publishing, mutating registrations and cleaning up are not protected by a transaction-wide lock. The new ownership check therefore does not prevent another installer from overwriting and deleting the recovery record.

**Executed scenario**, with the same inputs and in-memory journal storage as F1:

1. Beta loads the initially absent journal.
2. During beta’s first `provider.get("CLAUDE")`, suspend beta and run alpha.
3. Alpha publishes its manifest, removes both previous registrations, adds Claude and raises `RegistrationCrash` at `after_add_CLAUDE`.
4. Resume beta. It continues using its earlier `loaded=None`, publishes over alpha’s manifest, completes its own registration and deletes the shared journal.
5. Run beta again.

**Wrong output:** beta succeeds on both calls; alpha’s Codex registration remains absent; alpha’s journal has been deleted. Its previous-state recovery record is lost.

**Suggested fix:** acquire an exclusive cross-process lock before journal discovery and hold it through recovery, publication, mutations and cleanup. Atomic manifest replacement alone does not protect the transaction.

## SPEC COVERAGE

| Requirement | Status | Evidence |
|---|---|---|
| A1: accept completed original and interrupted restore prefixes | **done** | `host_config.py:1148` accepts the exact original and retains the prefix classifier. Requested recovery regression is present. |
| A2: recover valid prepared `manifest.next` without `manifest.json` | **done** | `host_config.py:997`, `:1192`: validates publication state, file identities and both originals before discarding. |
| A3: durable registration transaction and next-run recovery | **wrong** | Single-owner recovery exists, but F1 and F4 leave shared-instance recovery incomplete or destroy its record. |
| A3: PowerShell failed-Codex-add rollback seam | **done** | `install-mcp.ps1:60`, `:946`; the seam passed with execution-policy bypass. |
| Previous F1 | **wrong / partially fixed** | Sequential cross-instance corruption is prevented; owning-provider recovery and concurrent ownership protection remain incomplete. |
| Previous F2 | **done** | `install_mcp.py:1379` repairs pending host files before registration recovery or provider probes. |
| Previous F3 | **done** | `install_mcp.py:1192`, `:1210`: staged initial publication and incomplete-fragment handling cover the reported interruption. |

## GATE GAP

The tests do not interleave two installers between journal discovery and publication, so they miss F4.

The cross-instance regression at `tools/tests/test_g5_r9_journal.py:241` explicitly accepts `registration_journal_owner_mismatch`. It verifies preservation, but does not require the next unrelated installer to recover or proceed, so it misses F1.

The PowerShell seam exercises a successful fake removal; it does not test a failing native Claude removal.

## PREMISE

No material issue with the supplied baseline or treatment of unchanged gate failures.

The reported gate PASS does not establish safe shared-journal recovery. Its cross-instance test currently accepts the blocking behavior described above.

## NOT VERIFIED

- Full filesystem-backed suite and Python 3.11 rerun: the read-only sandbox prohibits the temporary file writes those tests require.
- Locally, the pure restore-classifier test passed. The PowerShell unittest failed because script execution is disabled; invoking the seam with `-ExecutionPolicy Bypass` passed.
- Both findings were reproduced through production registration functions with filesystem operations redirected to memory.
- No real registrations, process termination, in-game testing, or sealed-launcher rebuild.
- No files modified and no commit created.