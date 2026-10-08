VERDICT: CHANGES_REQUIRED

## FINDINGS

### F1 — P2 — Fixed-point equivalence still erases collision-producing completed identities

`tools/tests/test_storage_rotation_model.py:472`, `:532`, `:543`, `:567`, `:833`

The original occupied-`prepared` counterexample is fixed. However, published completions now lose their filename and txid:

```python
if replay is not None:
    published.append(replay)
    continue
```

Consequently, two snapshots with different launch continuations are still deduplicated. Eligible aborts also disappear without retaining the next usable retry position.

**Executed repro:** from `tools/`, run this through the project venv with `python -B -`:

```python
import json, os
from tests import test_storage_rotation_model as m

s = m.storage
original = m._canonical_marker(m.SEAL_OTHER)
a, world = m._completed_rotation(original)
name, record = next(
    (n, d) for n, d in m._published_journals(a)
    if d and d["phase"] == s.PHASE_MARKER_PUBLISHED
)
b = dict(a)
del b[m._norm(os.path.join(m.MISSION, name))]
other = dict(record, txid="9" * 32)
other_name = s.JOURNAL_PREFIX + other["txid"] + s.JOURNAL_COMPLETED_SUFFIX
b[m._norm(os.path.join(m.MISSION, other_name))] = (
    "file", json.dumps(other, sort_keys=True).encode(), other_name
)
for nodes in (a, b):
    nodes[m._norm(os.path.join(m.MISSION, s.STORAGE_NAME))] = ("dir", b"")
    nodes[m._norm(os.path.join(
        m.MISSION, s.STORAGE_NAME, "new.bin"
    ))] = ("file", b"new-world")

check = m.RotationModelTest()
for nodes in (a, b):
    check._check(nodes, world, original, None)
print("closure equal:", m._closure_key(a) == m._closure_key(b))
for nodes in (a, b):
    _, result = m._run(m.MemFS(), m.SEAL_X, m.TXID, nodes, None, None)
    print(result.launch_allowed, result.reason)
```

Observed:

```text
both oracle checks: PASS
closure equal: True
False backup_name_collision
True seal_changed
```

This violates the binding requirement to preserve collision-producing files and consumer-distinct states.

A second executed witness exposes the alias interaction: a valid `prepared` completed record named `STORAGE_1.modset.rotation.<txid>.completed.json`, with D/K absent, compares equal to no journal. Both oracle checks pass; production returns `journal_name_invalid` for the alias and `seal_changed` for the other snapshot.

**Required correction:** retain canonical identity and bytes for collision-producing records. Abstract only a validated consumed caller retry prefix, preserving allocation correctness and the next usable position.

### F2 — P2 — The required reseal/retry oracle never measures lifecycle replay

`tools/tests/test_storage_rotation_model.py:870`, `:1405`, `:1424`

The new replay regression checks `_closure_key` against itself. `_check` never invokes lifecycle replay or checks its measured observation. Thus the former M3 defect remains invisible to this required oracle.

**Executed mutation repro**, from `tools/` with `python -B -`:

```python
from tests import test_storage_rotation_model as m
from dayz_mcp import process_lifecycle as lifecycle

original = m._canonical_marker(m.SEAL_OTHER)
nodes, world = m._completed_rotation(original)
nodes, _ = m._run(
    m.MemFS(), m.SEAL_X, m.TXID, nodes, None, None
)
fs = m.MemFS()
fs.restore(nodes)
saved = m._install(fs)
real = lifecycle._pending_completed_rotation
try:
    print("actual replay:", real(m.MISSION, m.SEAL_X).storage_rotated)

    # Reintroduce the former M3 filtering mistake.
    def old_filter(mission, seal):
        record = m._published_journals(fs.nodes)[0][1]
        return None if record["new_seal"] != seal else real(mission, seal)

    lifecycle._pending_completed_rotation = old_filter
    print("mutant replay:",
          lifecycle._pending_completed_rotation(m.MISSION, m.SEAL_X))
    check = m.RotationModelTest()
    check._check(nodes, world, original, m.SEAL_X)
    check.test_f3_two_published_records_are_not_the_same_replay()
    print("oracle and new replay regression: PASS")
finally:
    lifecycle._pending_completed_rotation = real
    m._restore(saved)
```

Observed:

```text
actual replay: True
mutant replay: None
oracle and new replay regression: PASS
```

The completed record still carries A, the marker carries X, W is absent, and one reset remains pending. Current production replay works; the defect is the missing mandatory measurement and regression.

**Required correction:** exercise the fake lifecycle retry after resealing and assert reset visibility, the same D, and no duplicate rotation audit. Measure replay independently at the model boundaries.

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