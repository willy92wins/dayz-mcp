# 2986 parked (2026-10-08): language-independent player detection for dayz_test_close

Batch `r2a_855c_2986` (spec `SPEC_R2A.md`, item 2986; Grok 4.7 implementing, gpt-6.1-sol reviewing) ran four loop
rounds and one orchestrator round. 855c was split out and goes on alone. 2986 stops here by the loop's stop rule:
the same family of findings reappeared in every round, in the single-role warning scanner
(`_scan_player_markers` / `_player_state_warnings` in `tools/dayz_mcp/dayz_test_tool.py`):

| Round | Finding (same family: a completion or an empty list accepted while membership is unresolved) |
|---|---|
| r1 | an incomplete block can become a verified empty player list |
| r2 | an unrelated confirmation clears unresolved membership; homonyms lose identity |
| r3 | a reconnect inherits a verified empty membership; a trailing incomplete block disappears from warnings |
| r4 | a legacy `[Logout]` completion bypasses the trailing-block check |
| r5 (orchestrator fix) | a later confirmed connection inherits an older legacy close; a neutral `[Login]` read before a legacy close does not stop the early return |

What worked and stays as evidence in the batch workspace (`C:\Users\guill\dzmcp_gauntlet\r2a_855c_2986\ws`, not merged):
the neutral cursor ([Login]/[StateMachine] + `<CREATE NEW CHAR>`/`<LOAD EXISTING CHAR>` + correlated
`[Disconnect]: Finish`), the 1.29 and 1.30 fixtures, and the close's logout watcher, which the reviewer found to treat
the unresolved cases conservatively.

Design notes for the next attempt (to be decided, not started):
- One state machine for both formats. Feed the legacy `[Logout]: Player <id> finished` line into the neutral cursor as
  a completion correlated by player id, instead of a window-level regex that ends or overrides the walk.
- Alternatively, take membership from the bridge's player list at close time (the daemon can ask the server before
  closing the client) and use the RPT only for completion, which removes most of the parser's membership inference.
- Keep the long-log contract of fb62c5 (a pure-legacy log stops reading at the completion line).
