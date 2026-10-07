# The PowerShell installer registers through the Python registration transaction (owner decision, 2026-10-07)

Written by the orchestrator. The workspace is g5's final tree (independent instances plus its DZ-R9 fixes).
Never run an installer for real, pip, a registration, `claude`/`codex mcp`, a daemon, DayZ or an MCP tool, from
this tree or from any copy; tests use fake providers, temporary roots and the script's validation seams only.

## ITEM ps1-delegate

Today `tools/install-mcp.ps1 -Register` (`:900-947`) runs `claude mcp add/remove` and `codex mcp add/remove`
itself, without the installer lock, the registration journal or recovery that `tools/install_mcp.py`'s
`register_transaction` has; a kill between the two adds leaves the hosts half registered, a failed remove is not
checked, and with `-ReplaceExistingRegistration` a failed Codex add can leave no Claude registration at all (DZ-R9
findings N2, D2, E6). The owner decided: one transactional path.

- `install-mcp.ps1 -Register` keeps its own preparation, validation and decision steps (venv, `-Instance` and
  `-GamePath` validation, `Get-RegistrationReplaceDecision` and its refusal to replace an existing registration
  unless `-ReplaceExistingRegistration`), and performs the registration itself by running the venv's Python with
  `tools/install_mcp.py --register` and the matching flags (instance, game path, client executables, and option
  removal only when the replace decision requires it). It no longer calls `claude mcp add/remove` or
  `codex mcp add/remove` directly, and `Undo-DayZMcpClaudeRegistration` and its seam go away with that code.
- Exit code and output: the Python installer's exit code becomes the script's, and its error token is printed
  by name (for example `registration_busy`, `registration_journal_invalid`, `registration_would_drop_options`).
- Every flag the script accepted keeps working or is refused with a named message; the print-only default
  (without `-Register`) is unchanged.
- Tests (each must fail on this tree): the registration path of the script invokes `install_mcp.py --register`
  with the expected arguments for the default and a named instance (through a seam that replaces the Python
  invocation, never a real one); the script contains no direct `mcp add`/`mcp remove` call; a non-zero exit of
  the Python step propagates with its token; `-ValidateOnly` still exits before any side effect.
- Update `tools/README-mcp.md`'s install section if it describes the old behaviour.
