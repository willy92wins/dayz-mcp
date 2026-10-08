# Independent verification of seven claims (DayZ-MCP storage rotation recovery)

The current directory is a DayZ-MCP tree. For each claim below, open the cited lines and give TWO separate
verdicts, judged by you from the code only:
- `snippet_matches_file`: the cited location really contains the code the claim relies on (paste the relevant lines);
- `inference_holds`: the stated consequence follows from that code (say why, or give the counter-evidence).
You may run Python on in-memory or temporary-directory scenarios with the project's modules (read-only on this
tree). Do not start DayZ, daemons or MCP tools; do not touch `%LOCALAPPDATA%`, the home directory or real missions.

## Claims

C1. After a recovery that reseals A to X (the completed journal records new_seal=A while the canonical marker is
published with X), the lifecycle's completed-rotation replay skips that completed journal for a launch with seal X,
so a retried launch records storage_rotated=False although the reset happened.
Locations: tools/dayz_mcp/dayz_test_storage.py:943; tools/dayz_mcp/process_lifecycle.py:165, :3315, :3372, :4247.

C2. A recovery that aborts a prepared journal re-rotates with derived_txid(txid). If that rotation is killed before
the world moves, the next recovery aborts it and re-rotates with the same derived txid, whose completed journal
already exists, so the reservation check returns backup_name_collision on every later retry of that launch identity.
Locations: tools/dayz_mcp/process_lifecycle.py:3295-3299; tools/dayz_mcp/dayz_test_storage.py:1148-1151,
:1055-1062, :1095-1099, :529-533, :887-889.

C3. With storage_1 absent and a DIRECTORY named storage_1.modset.json, prepare_storage decides seal_only and
_publish_marker's os.replace raises, which the caller maps to storage_rotate_failed on every launch.
Locations: tools/dayz_mcp/dayz_test_storage.py:599-604, :1169-1170, :582-583; tools/dayz_mcp/process_lifecycle.py:3301-3302.

C4. _parse_json does not catch RecursionError: a deeply nested JSON under 64 KiB in a journal or marker makes
prepare_storage raise RecursionError on every launch, outside the exception types the caller catches.
Locations: tools/dayz_mcp/dayz_test_storage.py:285-291, :989; tools/dayz_mcp/process_lifecycle.py:3301-3302.

C5. After a crash in phase storage_moved or marker_published, an operator who renames the backup folder back to
storage_1 (or copies it, or deletes the backup) gets journal_state_impossible on every later launch, for every seal,
until the active journal file is removed; no shipped text names that remedy.
Locations: tools/dayz_mcp/dayz_test_storage.py:651-657, :919-920, :1139-1147; tools/dayz_mcp/process_lifecycle.py:1241-1247.

C6. dayz_test_run returns only storage_recovery_required (plus a static hint) for every recovery refusal; the
specific reason (journal_state_impossible, journal_unreadable, journal_name_invalid, journal_ambiguous) reaches only
the daemon audit row, and the storage_rotate_failed hint says the next call reconciles the journal.
Locations: tools/dayz_mcp/process_lifecycle.py:3329-3341, :4263-4265, :1232-1248.

C7. Any file whose name starts with the journal prefix but does not match the active-journal grammar (for example a
.bak copy of a journal) makes every launch return journal_name_invalid until it is removed.
Locations: tools/dayz_mcp/dayz_test_storage.py:1014-1015, :1134-1135.

## Answer
A table `claim | snippet_matches_file | inference_holds | evidence (lines pasted or scenario run)`, then one short
paragraph per claim with the evidence. Answer in English.
