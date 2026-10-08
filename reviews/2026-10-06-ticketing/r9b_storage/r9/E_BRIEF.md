# Data audit E: DayZ-MCP mission storage rotation recovery (r9b): recovery paths, refusals and the operator

Several reviewers and an in-memory model check found this change acceptable. Be the auditor who assumes they
were wrong: re-verify every "this is safe because X" in the code. A bug here equals a lost or corrupted DayZ world
(the mission's `storage_1`), a marker that names the wrong modset, or a launch that can never start again.

## Load
- `_audit_context/CONTEXT.md` (what changed, decisions, boundaries), `_audit_context/SPEC_R9B_IMPL.md` (the
  binding state machine), `_audit_context/R9B.diff`, `_audit_context/TICKET_c5ac.md`, `_audit_context/SOL_R4.md`.
- The code in this directory (`tools/dayz_mcp/dayz_test_storage.py`, `tools/dayz_mcp/process_lifecycle.py`,
  `tools/dayz_mcp/dayz_test_tool.py` and whatever calls `prepare_storage`); use grep.

## Angle (yours only)
Recovery paths, refusals and what the caller and the operator can do with them:
- Every refusal the recovery can return (for example `journal_state_impossible`, alias refusals, legacy-state
  refusals): which state produces it, what reaches the `dayz_test_run` caller (error code, rotation report fields
  such as `storage_rotated`, `storage_backup`, `storage_reset_notice`), and whether the operator has a documented,
  safe way out. A refusal that blocks every future launch of that mission with no named way out is a finding
  (livelock), and so is a way out that would destroy the original world.
- The three scenarios of DZ-R8 walked with the verbs the operator really has (MCP tools, files on disk): the happy
  rotation (modset A to B to A), a crash at each phase followed by a launch with the SAME seal and with a
  DIFFERENT seal, and an operator who deletes or restores a backup folder by hand after a crash.
- The consumers of the rotation result: the run record, the observation log with `launch_operation_id`, the
  rotation report in the `dayz_test_run` answer, and anything that later reads `storage_backup`.

## Report: AUDIT.md in the workspace root
Sections exactly: `## FINDINGS`, `## SCENARIOS` (the three walks, one paragraph each), `## NOT VERIFIED`. Every
finding: id (E1...), P0-P3, the LITERAL code with `path:line_start-line_end`, the reasoning, and an executable
failure scenario (a temporary-directory script is best); a finding without a snippet is dropped. Defensive code is
not a bug. At most 1500 words.

## Boundaries
Read-only except AUDIT.md (scratch under `_scratch/`, deleted before you finish). Python only with the project
venv on in-memory or temporary-directory scenarios:
`"/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe"`.
Never run an installer, pip, a daemon, DayZ, git or an MCP tool; never touch %LOCALAPPDATA%, the home directory,
a real mission folder or MCP configuration. Time ceiling about 60 minutes; write AUDIT.md early and fill it as
you go.
