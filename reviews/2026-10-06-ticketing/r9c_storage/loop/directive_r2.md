The workspace already holds the eight production changes of the specification (from r9b round 6). Complete the
batch: (1) every regression test the specification lists, each failing on the tree without its production change
and passing now; (2) every model-oracle extension the specification names (alias spelling preserved in the model,
retained abort identities not erased by the fixed-point equivalence, same-identity recovery paths, artifact kinds,
bounded short reads and streaming digests, measured replay after a reseal, refusal witnesses with the public
diagnostic pair); (3) the existing envelope tests migrated to storage_recovery_reason (null for success and
unrelated failures); (4) docs size claims true (PROJECT-MAP), no CHANGELOG edit; (5) the packaged lock regenerated.
Run the whole fast tier before you finish: it must be green. Write a `ROUND 2 FIXES` section in REPORT.md. Never run
DayZ, a daemon, an MCP tool, an installer, pip or git.
