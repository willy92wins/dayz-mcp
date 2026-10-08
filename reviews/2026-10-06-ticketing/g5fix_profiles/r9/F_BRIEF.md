# Data audit F: DayZ-MCP named-instance profile folders (g5fix): close, logout wait and concurrent runs

Several reviewers found this change acceptable. Be the auditor who assumes they were wrong: re-verify every "this is
safe because X" in the code. A bug here equals a server stopped before a player's last state was saved, a close that
reports success it did not observe, or one instance reading or acting on another instance's run.

## Load
- `_audit_context/CONTEXT.md`, `_audit_context/SPEC_G5FIX.md`, `_audit_context/G5FIX.diff`, `_audit_context/SOL_R3.md`.
- The code in this directory (`tools/dayz_mcp/dayz_test_tool.py`, `launch_logs.py`, `log_tail.py`, `server.py`,
  `process_lifecycle.py`); use grep.

## Angle (yours only)
The close and the readers of an existing run, for the default instance AND a named one:
- `dayz_test_close` and `dayz_test_stop`: where the RPT watches and the logout boundary come from, what happens when
  the recorded anchor is refused (does the close then skip the logout wait silently, kill without waiting, or refuse
  with a named error?), RPT rotation during the close, a client retired before the server, and a run recorded by an
  older daemon (legacy anchor) closed by this code after an upgrade.
- Two instances on one host: can a reader of instance A resolve, wait on or report a file of instance B (same
  project dev root, different token; same token, different project)?
- Walk DZ-R8's three scenarios with the verbs the operator really has (dayz_test_run / close / stop, wait_for,
  logs_since): the happy path on a named instance; a daemon killed between launch and close then restarted; an
  operator who deletes or renames a `profiles-<token>` folder while a run is live.

## Report: AUDIT.md in the workspace root
Sections exactly: `## FINDINGS`, `## SCENARIOS`, `## NOT VERIFIED`. Every finding: id (F1...), P0-P3, the LITERAL code
with `path:line_start-line_end`, the reasoning, and an executable failure scenario (in-memory or temporary-directory
script); a finding without a snippet is dropped. Defensive code is not a bug. At most 1500 words.

## Boundaries
Read-only except AUDIT.md (scratch under `_scratch/`, deleted before you finish). Python only with the project venv on
in-memory or temporary-directory scenarios:
`"/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe"`.
Never run an installer, pip, a daemon, DayZ, git or an MCP tool; never touch %LOCALAPPDATA%, C:\temp\DayZ_MCP_130*,
the home directory or MCP configuration. Time ceiling about 60 minutes; write AUDIT.md early and fill it as you go.
