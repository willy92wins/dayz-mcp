Fix exactly F1 of the round-3 review. The reviewer closed F2, F3 and F4 and confirmed the production changes:
do not touch any file under tools/dayz_mcp/, the packaged-modules lock, addon/ or the existing storage tests.
This round changes the model oracle only (tools/tests/test_storage_rotation_model.py).

- F1 (P1, the model gate): a completed journal C is accepted by the oracle only in the two phases that constitute
  completion: `prepared` (an aborted rotation) and `marker_published` (a finished rotation). Check that phase
  independently of the production validator (which accepts `storage_moved` for an active journal) and before the
  closure equivalence is applied; any other completed phase, `storage_moved` included, fails the check with a
  message that names the phase.
- Negative control (must FAIL the oracle): the reviewer's atomic wrong-phase mutation. Wrap `_complete_journal` so
  that, after the original call, a completed rotation that recorded an absent original marker
  (`old_marker_state == storage.MARKER_ABSENT`, phase `storage.PHASE_MARKER_PUBLISHED`) is rewritten atomically
  to phase `storage.PHASE_STORAGE_MOVED` (`storage._write_json_atomic(completed_path, doc, replace=True)`).
  The test passes only when the gate reports that mutation as a failure.
- Positive check: the reviewer's single-state counter-example (schema-2 C, txid "1"*32, phase storage_moved,
  new_seal "a"*64, old_seal None, project DayZ_MCP, old_marker_state absent, D=storage_1.modset-d3-legacy holding
  O, K absent, W and J absent, M holding canonical N) makes `_check(nodes, original_world, None, SEAL_A)` fail.
- Every existing test keeps its result; the gate's baseline failures stay the only failures. Write a
  `ROUND 4 FIXES` section in REPORT.md. Never run DayZ, a daemon, an MCP tool, an installer, pip or git.
