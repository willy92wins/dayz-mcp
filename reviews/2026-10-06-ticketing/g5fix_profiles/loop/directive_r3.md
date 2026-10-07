Fix exactly F1 and F2 of the round-2 review. Everything else the reviewer marked done stays as it is, and the
specification stays binding. This is the last implementation round.

- F1 (P2, fail-open project check): `_recorded_anchor_in_sealed_project` (tools/dayz_mcp/launch_logs.py) must
  reject a run row whose project cannot be resolved (missing, empty or non-string `mod`, or a mod that maps to no
  sealed project), exactly as it rejects a wrong project root. The readers that use it (`logs_since`,
  `_wait_for_script_log_paths`, the capture-window resolution in server.py) then treat that row as having no
  admissible folder. Update the positive log fixtures so they carry a valid project identity; do not keep a
  production bypass for incomplete fixtures. Regression: the reviewer's row (run "R", RUNNING, profiles
  C:\unapproved\_server\profiles-130, no mod) makes logs_since return nothing from that folder and log_matches stay
  unsatisfied, with the sealed-project loader consulted.
- F2 (P2, the gate failure): the strengthened client-extension contract (a valid recorded profiles anchor is
  required) broke existing fixtures. Adapt them, preserving every original assertion: give each affected fixture a
  complete approved policy (with dev_root) and a valid recorded anchor (for the default instance,
  <dev_root>\_server\profiles). Affected: tests/test_steam_not_running_preflight.py,
  tests/test_db05_preflight_diagnostics.py, tests/test_dayz_test_tool_modes.py
  (ModeContractM19Test.test_the_dead_client_check_follows_a_substituted_authority) and
  tests/test_client_lifecycle_7055_9336_9efc.py (ClientExtensionAdmissionTest). Keep the invalid-anchor rejection
  intact; do not weaken production code to make an old fixture pass. Run those four modules and every module of
  the batch gate before you finish; all must pass.
- F3 (launcher reseal and bundle verification) is NOT yours: the orchestrator rebuilds and reseals the launcher
  at deployment and records the verification there. Keep tools/packaged-modules.lock.json regenerated for any
  sealed module you change.
- Write a `ROUND 3 FIXES` section in REPORT.md. Never run DayZ, a daemon, an MCP tool, an installer, pip or git.
