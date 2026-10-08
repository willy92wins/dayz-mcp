Round 3 is the last round of this batch. Production stays as it is unless a test proves a change wrong. Fix the
round-2 findings F1 and F2 (appended verbatim below) and complete every coverage row the reviewer marked Missing,
Wrong or partial in the table that follows. In particular: the fixed-point equivalence keeps the canonical name,
txid and bytes of every completed record that can change a continuation (published completions included) and
abstracts only a validated consumed caller retry prefix while keeping the next usable position; the oracle
invokes lifecycle replay at the model boundaries and a retry after a reseal asserts storage_rotated=true, the same
backup and no duplicate rotation audit (a mutant that reintroduces the old new_seal filter must fail it); D1 runs
same-caller t1/t2/t3 abandonment paths instead of fresh literal identities; M2 and D2 test ordinary
classification with no active journal, with marker variants and link/other kinds. Each new test must fail
against the mutation it guards. Keep the fast tier green, the packaged lock regenerated and CHANGELOG untouched.
Write a `ROUND 3 FIXES` section in REPORT.md. Never run DayZ, a daemon, an MCP tool, an installer, pip or git.

### The reviewer's coverage table (round 2)
## SPEC COVERAGE

| Specification bullet | Status |
|---|---|
| Preserve existing production changes, schemas, naming, recovery graph and lifecycle scope | **Done for preservation.** No production redesign identified in the consolidation. |
| Previous F1 occupied-`prepared` witness | **Done for that witness; wrong overall.** F1 above demonstrates another collision identity erased. |
| Previous F2 directory-entry spelling | **Done.** The uppercase active-journal regression passes. |
| Previous F3 envelope migration | **Done.** The migrated envelopes assert null for success and unrelated failures. |
| Previous F4 sizes and CHANGELOG scope | **Done.** Size test passes; CHANGELOG is byte-identical to `base_v11d`. |
| M1 regressions and independent family oracle | **Missing in part.** Spelling is preserved, but full alias parameterization and independent malformed-family discovery remain incomplete. |
| M2 ordinary unsupported-W regressions and artifact-kind oracle | **Missing.** Existing file-W refusal fixture contains an active journal; it does not test ordinary classification. Required marker variants and link/other coverage are absent. |
| M3 reseal/retry lifecycle regression and measured oracle | **Missing/wrong.** F2 above. Required lifecycle controls are incomplete. |
| D1 same-caller t₂/t₃ abandonment regressions | **Missing.** The cut exploration still uses fresh `"2"`, `"3"`, and `"4"` caller identities. |
| D1 allocation, retained-record immutability and eligible-prefix abstraction | **Missing/wrong.** Next usable position is erased; required independent allocation assertions are absent. |
| D2 ordinary unsupported-M and lifecycle diagnostics | **Missing.** Required absent/present-W and link/other controls are absent. |
| D3 nested active journal | **Done by test presence.** Filesystem execution was blocked locally. |
| D3 oversized original preservation and positive short reads | **Done in part.** Full-marker hash/preservation test exists; one-byte publication reads pass. |
| D3 nested marker, journal cap spy, tail-only corruption and short-digest cut exploration | **Missing.** |
| E2 complete refusal → broker → worker → serialization → parser → public-result regressions | **Missing.** The added test constructs `WorkerTerminal` directly for one reason. |
| E2 healthy/degraded cleanup, legacy/malformed terminals and refusal-witness public oracle | **Missing.** |
| E1/E3 shipped procedure and README link | **Done for presence.** The document contains the principal recovery instructions. |
| E1/E3 restore/quarantine scenarios and explicit external model transitions | **Missing.** |
| Exact snapshot dedup retains completed names/bytes | **Done for completed file identities.** |
| Fixed-point consumer equivalence | **Wrong.** F1; moreover, previous membership assertions were replaced with `shapes.add(...)` at `:845` and `:851`. |
| Original producer → X → Y cut exploration | **Done.** The depth-two test passes locally. |
| Every specified red regression and oracle extension | **Missing.** The directive was not fully completed. |
| Packaged lock | **Done as an artifact.** Current pinned-source hashes match. REPORT explicitly says regeneration was not rerun. |
| Whole fast tier | **Done as the supplied baseline-relative gate.** `6032` tests, two baseline failures, zero new failures. |
| `ROUND 2 FIXES` report | **Done.** Its completion account does not establish the outstanding mandatory coverage. |

## GATE GAP

The gate can pass while:

- closure deduplication merges snapshots with opposite launch decisions;
- reset visibility disappears after resealing;
- repeated deaths under the same caller identity exhaust allocation;
- ordinary file/link/other classification regresses;
- authentication hashes only the bounded JSON prefix;
- worker or launcher serialization drops storage diagnostics.

The new tests do not observe several of these required consumer boundaries. The passing depth-two test also no longer proves fixed-point membership.

## PREMISE

- The supplied `DIFF.patch` omits the changed README and new `docs/STORAGE_RECOVERY.md`. I inspected their live contents and compared them with `base_v11d`.
- “Fast tier must be green” means baseline-relative green here: the supplied gate retains two baseline failures. Those are not new batch defects.
- The eight production changes being present does not establish that the explicitly required regression/model work is complete.

This is the final bounded review: remaining blockers return to the orchestrator; no additional fix round was started.

## NOT VERIFIED

Locally, **86 focused tests passed**, including envelopes, docs sizes, lock checks and focused model regressions. The **depth-two model test passed** separately.

Filesystem-based tests could not complete because the read-only sandbox prevents temporary-directory creation. Their resulting environment errors are not reported as product failures. I did not rerun the whole fast tier; its result is the orchestrator-supplied evidence.

No files were modified. No Git, DayZ, daemon, MCP, installer, or deployment operation was performed.
