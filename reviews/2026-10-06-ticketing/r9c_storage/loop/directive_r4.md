Round 4 is the final round of this batch. Production stays exactly as it is: the reviewer confirmed it passes every
positive control. Fix only the round-3 test findings F1-F4 (appended verbatim below), each as the reviewer's
"Correction" says, and make each new or changed test fail against the in-memory mutation the reviewer used to show the
gap:
- F1: keep the artifact kind (file, link, other) in both _closure_key and _auth_key; only real canonical files enter the
  eligible-prefix abstraction; add the file/link continuation counterexample as a regression.
- F2: produce the retained aborts through actual same-caller deaths (abandon before W->D, retry with the same caller,
  consecutive deaths to t2/t3), compute the expected ids independently of production's derived_txid, and enumerate cuts
  around the allocation and recovery I/O.
- F3: write the corrupted M/K bytes back and run recovery on them; require refusal with an identical complete
  snapshot; add cuts with positive short digest reads; the journal spy asserts total bytes consumed.
- F4: drive refused lifecycle starts through the fake broker, the real worker _start, cleanup and the launcher's
  exception serialization, then parse and assemble the public result; couple them to refusal snapshots and zero spawn.
F5 (P3) only if it costs nothing extra. Keep the fast tier green, the packaged lock unchanged unless a packaged file
changes, CHANGELOG untouched. Write a `ROUND 4 FIXES` section in REPORT.md. Never run DayZ, a daemon, an MCP tool, an
installer, pip or git.
