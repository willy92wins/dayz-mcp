Fix exactly F1-F4 of the round-2 review; keep everything else the reviewer confirmed (the design in the spec stays
binding). This is the last implementation round.

- F1 (P1, the model gate): strengthen the oracle so each executed counter-example fails: authenticate E at the
  exact reserved M/K locations for the phase (never "any *.marker.json"); a completed rotation with an original
  marker requires E in K even when E equals N; validate every published document (journal, markers) after every
  cut; and explore every continuation of the closure check unconditionally (remove the `_shape(recovered) ==
  _shape(state)` skip), with the equivalence used for deduplication defined explicitly. Add the reviewer's three
  mutations as negative controls that must FAIL the oracle (wrong K name, E only in M for E=N, malformed
  completed journal).
- F2 (P1, `dayz_test_storage.py:491`): compare authoritative names by filesystem identity on Windows, not by
  casefold alone: a name equal to a reserved one after Win32 normalization (case, trailing periods, trailing
  spaces) is refused BEFORE any mutation. Regression: the reviewer's `storage_1.` scenario refuses with an
  identical snapshot and zero renames.
- F3 (P1, `:754-762`): refuse an intact prepared state (world W present, schema-1 journal) whose canonical marker
  already carries the new seal while K holds the old one, before completing the journal; keep the preserved-K
  exceptions only for the moved-world legacy states. Regression: the reviewer's input refuses, journal and world
  untouched.
- F4 (P2, `:951`): same-seal recovery finishes N with the JOURNAL's project, reseals when the caller's seal
  differs, and confirms seal X without rejecting a different project label; the successful rotation result is
  preserved. Regression: the reviewer's input succeeds once and reports the rotation.
- Keep the packaged-modules lock regenerated; never run DayZ, a daemon, an MCP tool, an installer, pip or git.
