# Data audit D: DayZ-MCP mission storage rotation recovery (r9b): persistence and atomic flow on a real disk

Several reviewers and an in-memory model check found this change acceptable. Be the auditor who assumes they
were wrong: re-verify every "this is safe because X" in the code. A bug here equals a lost or corrupted DayZ world
(the mission's `storage_1`), a marker that names the wrong modset, or a launch that can never start again.

## Load
- `_audit_context/CONTEXT.md` (what changed, decisions, boundaries), `_audit_context/SPEC_R9B_IMPL.md` (the
  binding state machine), `_audit_context/R9B.diff`, `_audit_context/TICKET_c5ac.md`, `_audit_context/SOL_R4.md`.
- The code in this directory (`tools/dayz_mcp/dayz_test_storage.py`, `tools/dayz_mcp/process_lifecycle.py`,
  `tools/tests/test_storage_rotation_model.py`); use grep.

## Angle (yours only)
Persistence and atomic flow ON A REAL WINDOWS DISK, which the in-memory model does not cover: every write,
flush, fsync, rename, replace, unlink and directory move the producer and the recovery perform. For each, say
what NTFS does that the in-memory filesystem does not: case-insensitive collisions, 8.3 short names, rename over
an existing file or directory, a directory move across volumes, a sharing violation because DayZ, OneDrive, an
indexer or an antivirus holds a handle, a partial directory copy, a replace that is not atomic, an fsync that
does not reach the directory entry. Walk the crash matrix for each step (kill the process between every pair of
I/O steps): what is on disk, and what the next `prepare_storage` does with it. A state the next run cannot
recover, or recovers by losing or overwriting the original world, is a finding. Also: input bounds of every
persisted document read (journal, markers): malformed, oversized, hostile.

## Report: AUDIT.md in the workspace root
Sections exactly: `## FINDINGS`, `## CRASH MATRIX`, `## NOT VERIFIED`. Every finding: id (D1...), P0-P3, the
LITERAL code with `path:line_start-line_end`, the reasoning, and an executable failure scenario (a temporary-
directory script is best); a finding without a snippet is dropped. Defensive code (tolerant parsing, redundant
guards) is not a bug. At most 1500 words.

## Boundaries
Read-only except AUDIT.md (scratch under `_scratch/`, deleted before you finish). Python only with the project
venv on in-memory or temporary-directory scenarios:
`"/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe"`.
Never run an installer, pip, a daemon, DayZ, git or an MCP tool; never touch %LOCALAPPDATA%, the home directory,
a real mission folder or MCP configuration. Time ceiling about 60 minutes; write AUDIT.md early and fill it as
you go.
