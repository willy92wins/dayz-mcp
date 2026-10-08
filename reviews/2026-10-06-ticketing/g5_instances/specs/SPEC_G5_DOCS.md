# Operator documentation for independent instances (batch g5)

Written by the orchestrator. The workspace is g5's approved tree plus its DZ-R9 fixes (journal, locks,
selection), composed and gated. The code is the source of truth; the docs describe it. Every statement you
write must be backed by code you opened: list each claim with its `path:line` in REPORT.md under
`## CLAIMS` (claim, `path:line`, what that line shows). A claim you cannot back is left out, not guessed.

## ITEM g5-docs

Today the only user-facing text about named instances is one CHANGELOG line. Add:

1. `tools/README-mcp.md`: a new section `## Independent instances` after `## Install`, in the file's style
   (English, short paragraphs and lists, code spans for flags, paths and error codes). It must answer, from
   the code:
   - what a named instance is: the token grammar and the reserved `default`; what changes per instance
     (state root `%LOCALAPPDATA%\DayZ_MCP_<token>`, port, keyfile, registration name `dayz-mcp-<token>`,
     game path) and what stays shared (the physical box admission, the build locks, the host-config journal,
     the shared root `DAYZ_MCP_SHARED_ROOT` or `%LOCALAPPDATA%\DayZ_MCP_shared`, which must be absolute);
   - how to create one: a separate tools tree per instance (owner decision), then the installer with
     `--instance <token> --game-path <absolute DayZ folder>` (Python `install_mcp.py --register` and
     `install-mcp.ps1 -Instance/-GamePath`), how the selector may be given (flag, environment) and what a
     conflict between them answers;
   - adopting an existing legacy store (a tools tree hardcoded to the same state root): what the daemon checks
     before migrating (quiescence: which processes block and which do not), the receipt, what an operator does
     when the migration is blocked, and that after a settled receipt a live same-root writer still blocks every
     start;
   - one writer per state root (the lifetime lease) and what a second writer sees;
   - the new error codes an operator can meet, one line each with the action to take: at least
     `registration_busy`, `build_busy`, the relative shared root refusal, `instance_environment_conflict`,
     `duplicate_instance_flag`, `glued_instance_flag`, `state_root_writer_busy`, and the migration blockers.
2. `product-spec.md`: one acceptance row `H16` in the H table, in Spanish like its neighbours, with the
   observable criteria of independent instances (two instances on one host with separate state, registration
   and port; one box admission across them; a legacy store adopted only when quiescent; selector conflicts
   refused before any effect). Keep the table's column layout.
3. `CHANGELOG.md`: keep the existing line; add a pointer to the new README section only if the line does not
   already say where to read more.

Do not change code. `tests.test_docs_truth` must pass (it checks README claims against the code, for example
the lease list and size claims in PROJECT-MAP.md; update the PROJECT-MAP size claim of a file you grow when the
test asks for it).
