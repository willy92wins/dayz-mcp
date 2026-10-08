# Data audit G: DayZ-MCP named-instance profile folders (g5fix): provisioning, credentials and fail-closed paths

Several reviewers found this change acceptable. Be the auditor who assumes they were wrong: re-verify every "this is
safe because X" in the code. A bug here equals the bridge key written where it should not be, a launch admitted on a
folder it does not own, or a refusal that fails open.

## Load
- `_audit_context/CONTEXT.md`, `_audit_context/SPEC_G5FIX.md`, `_audit_context/G5FIX.diff`, `_audit_context/SOL_R3.md`.
- The code in this directory (`tools/dayz_mcp/loopback.py` `ServerState.prepare` and `_seed_bridge_config`,
  `server_cli.py`, `dayz_test_worker.py`, `dayz_test_attestation.py`, `native_launcher_transaction.py`); use grep.

## Angle (yours only)
Provisioning and every fail-closed path:
- `ServerState.prepare` for a named instance: a mistyped dev_root, a missing role root, a file or a junction/symbolic
  link at the leaf or at an ancestor, a junction that redirects the leaf outside the project, a concurrent creation, a
  leaf that already holds another instance's `dayz_mcp.json` (endpoint or key mismatch), an unwritable folder. For each:
  is the key ever written outside the intended folder, is anything created besides the leaf, and is the error typed?
- Token authority: can a token come from anything but the sealed request (worker, VPP) or the bound context (daemon)?
  An environment variable, a recorded run, a directory listing, or the existing config contents?
- The default instance: is anything it did before now different (argv strings, artifact order, config bytes, missing
  `profiles` folder behaviour)?
- Input bounds of every persisted document the change reads (recorded run `profiles`, `dayz_mcp.json`): malformed,
  oversized, hostile.

## Report: AUDIT.md in the workspace root
Sections exactly: `## FINDINGS`, `## FAIL-CLOSED MATRIX` (case -> observed behaviour), `## NOT VERIFIED`. Every finding:
id (G1...), P0-P3, the LITERAL code with `path:line_start-line_end`, the reasoning, and an executable failure scenario
(temporary-directory script); a finding without a snippet is dropped. Defensive code is not a bug. At most 1500 words.

## Boundaries
Read-only except AUDIT.md (scratch under `_scratch/`, deleted before you finish). Python only with the project venv on
in-memory or temporary-directory scenarios:
`"/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe"`.
Never run an installer, pip, a daemon, DayZ, git or an MCP tool; never touch %LOCALAPPDATA%, C:\temp\DayZ_MCP_130*,
the home directory or MCP configuration. Time ceiling about 60 minutes; write AUDIT.md early and fill it as you go.
