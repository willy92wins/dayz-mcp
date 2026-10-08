VERDICT: CHANGES_REQUIRED

## FINDINGS

The blocking defects concern the required regression/model work. Unmodified production passed the corresponding positive controls. Mutations below were applied only in process memory; no files were changed.

### F1 — P2 — Both deduplication keys erase collision-producing artifact kinds

`tools/tests/test_storage_rotation_model.py:567`, `:595`, `:640`, `:653`

`_closure_key` treats a completed link/other node containing valid journal bytes as an eligible file abort. `_auth_key` also omits the distinction between file, link and other.

**Executed witness**, from `tools/` with the project venv and `python -B -`:

```python
import os, json, hashlib
from tests import test_storage_rotation_model as m

s = m.storage
original = m._canonical_marker(m.SEAL_OTHER)
base = m._seed_nodes(original)
body = m._journal(
    m.TXID, s.PHASE_PREPARED,
    old_seal=m.SEAL_OTHER,
    old_marker_state=s.MARKER_PRESENT_VALID,
    old_marker_sha256=hashlib.sha256(original).hexdigest(),
    storage_backup="storage_1.modset-spent",
    marker_backup="storage_1.modset-spent.marker.json",
)
name = m._canonical_completed_name(m.TXID)
key = m._norm(os.path.join(m.MISSION, name))
raw = json.dumps(body, sort_keys=True).encode()
a, b = dict(base), dict(base)
a[key] = ("file", raw, name)
b[key] = ("link", raw, name)

world = m._tree(base, os.path.join(m.MISSION, s.STORAGE_NAME))
for nodes in (a, b):
    m.RotationModelTest()._check(nodes, world, original, None)

print(m._closure_key(a) == m._closure_key(b))
print(m._auth_key(a) == m._auth_key(b))
for nodes in (a, b):
    _, result = m._run(
        m.MemFS(), m.SEAL_A, m.TXID, nodes, None, None
    )
    print(result.launch_allowed, result.reason)
```

Observed:

```text
oracle checks: both PASS
closure equal: True
exact key equal: True
file True seal_changed
link False backup_name_collision
```

Thus both deduplication stages merge states with opposite launch decisions. This violates the directive to preserve collision-producing completed records and unsupported types.

**Correction:** retain artifact kind in both keys; only actual canonical files may enter eligible-prefix abstraction. Add this continuation counterexample as a regression.

### F2 — P2 — D1 still does not test same-caller abandonment paths

`tools/tests/test_storage_rotation_model.py:867`, `:1891`, `:1904`, `:1953`

The new D1 test directly plants completed `t₀/t₁` or `t₀/t₁/t₂`. It never abandons an active transaction. The cut enumeration still recovers under fresh literal caller identities. Expected retry IDs also come from production’s `derived_txid`, rather than an independent calculation.

**Executed scenario:**

1. Seed W and the original marker with an intact schema-2 prepared J at `t₀ = "1"*32`.
2. Invoke recovery with the same caller, seal A and fixed time.
3. Abruptly abandon immediately before W→D. The resulting active journal is:

```text
storage_1.modset.rotation.b4aff1986b237f81b11bd81f6fee906f.json
```

4. Retry with the same caller. Current production succeeds and completes independently calculated t₂.
5. Apply an in-memory mutation that refuses reconciliation of this active t₁ with `backup_name_collision`.

Observed:

```text
healthy retry: True seal_changed; t2 completed: True
mutant retry: False backup_name_collision
test_r3_d1_same_caller_walks_t1_t2_and_t3: PASS
test_r3_f1_eligible_prefix_keeps_the_next_usable_id: PASS
```

The named D1 test was executed using its unchanged body with temporary paths redirected to MemFS because native temporary-directory creation is prohibited.

**Correction:** generate retained aborts through actual same-caller deaths, including consecutive deaths leading to t₂/t₃, and enumerate cuts around the new allocation/recovery I/O.

### F3 — P2 — The tail-corruption test never submits corrupted bytes to recovery

`tools/tests/test_storage_rotation_model.py:2068`, `:2071`

The test changes a local bytearray and checks that `hashlib.sha256` changes. It does not write the corrupted bytes back or invoke recovery with them.

**Executed witness:**

- E is a canonical marker for `SEAL_OTHER`, padded to 65,536 bytes, followed by `b"TAIL"`.
- Plant schema-2 J in `storage_moved`, W absent, D containing the original world, M absent, and K containing E with only its final byte flipped.
- J records SHA-256 of the complete, uncorrupted E.

Current production refuses:

```text
False journal_state_impossible
```

Apply this in-memory mutation:

```python
real = storage._select_schema2

def mutant(document, **kw):
    for field in ("m", "k"):
        head = kw[field + "_head"]
        if head is not None and len(head) > storage._MAX_JSON_BYTES:
            kw[field + "_sha"] = document.get("old_marker_sha256")
    return real(document, **kw)
```

Observed:

```text
mutant corrupted-tail recovery: True recovered_storage_moved
test_r3_d3_cap_spies_nested_marker_and_tail: PASS
```

Again, the unchanged test body ran against virtual temporary paths. The required authentication negative therefore remains absent.

**Correction:** recover from corrupted M/K fixtures and require refusal with an identical complete snapshot. Add the required cuts with positive short digest reads. The journal spy must also assert total bytes consumed, not merely each requested read size.

### F4 — P2 — E2 bypasses the worker start and launcher exception boundaries

`tools/tests/test_storage_rotation_model.py:2115`, `:2121`, `:2123`, `:2147`

The “broker” is a dictionary passed directly to `_storage_recovery_pair`; the test constructs `_failed` itself and calls `_write_worker_terminal` directly. It never invokes worker `_start` or the launcher’s exception-handling path.

**Executed mutation:** compile worker `_start` in memory with:

```python
source = inspect.getsource(worker._start)
source = source.replace(
    'if rejection == "storage_recovery_required"',
    'if False',
)
exec(compile(source, "<mutant>", "exec"), worker.__dict__)
```

A fake broker returns:

```python
{
    "ok": False,
    "error": "storage_recovery_required",
    "storage_recovery_reason": "journal_unreadable",
    "storage_recovery_hint":
        storage.storage_recovery_hint("journal_unreadable"),
}
```

Running the existing fake-broker worker harness produced:

```text
healthy: storage_recovery_required, reason=journal_unreadable, spawned=0
mutant:  storage_recovery_required, reason=None, spawned=0
new E2 regression under mutant: PASS
```

The regression misses precisely the transport boundary this batch must protect.

**Correction:** drive refused lifecycle starts through the fake broker and real worker start, cleanup and launcher exception serialization, then parse and assemble the public result. Couple those checks to refusal snapshots and zero-spawn assertions.

### F5 — P3 — Other binding acceptance checks remain incomplete

`tools/tests/test_storage_rotation_model.py:911`, `:917`, `:1867`, `:2208`, `:2258`

- The fixed-point membership assertions remain replaced by `shapes.add(...)`; novel continuation keys are accepted without proving closure.
- M1’s canonical “positive control” contains `{}` and returns `journal_unreadable`; it does not demonstrate canonical-name recovery.
- Operator restoration covers one `storage_moved` case, but not the required `marker_published` restoration and K authentication/layout controls or explicit external transitions in the model.
- The shipped-document test checks a small phrase list, rather than all required phase-specific procedures.

These are static coverage findings without an additional executed failure witness, hence P3.

## SPEC COVERAGE

| Requirement | Status |
|---|---|
| Preserve production, schemas, naming, recovery graph and lifecycle scope | **Done.** Production diff sections are unchanged from round 2. |
| Previous F1 published filename/txid and completed-alias witnesses | **Done for those witnesses.** Overall equivalence remains **wrong**: F1. |
| Previous F2 measured reseal/retry regression | **Done.** Actual lifecycle retry reports the same reset and backup without another rotation audit. The old `new_seal` filter mutant fails it. |
| Directory-entry spelling preservation | **Done.** |
| Existing envelope migration and null behavior | **Done.** All four envelope tests passed locally. |
| Documentation sizes and untouched CHANGELOG | **Done.** Size test passed; CHANGELOG matches `base_v11d` by SHA-256. |
| M1 alias parameterization and independent discovery | **Done for negative fixtures; missing valid canonical recovery control.** |
| M2 ordinary file/link/other W, marker variants and immutability | **Done for regressions; artifact-kind deduplication wrong:** F1. |
| M3 replay measurement at model boundaries | **Done for absent-W, valid-marker boundaries.** |
| M3 reseal visibility, same backup and no duplicate audit | **Done.** Complete requested control matrix remains **missing in part**. |
| D1 same-caller t₂/t₃ abandonment paths | **Missing:** F2. |
| D1 independent allocation, retained-byte checks and eligible-prefix abstraction | **Wrong/incomplete:** F1–F2. Steady-state retained-byte checks exist. |
| D2 ordinary unsupported M with absent/present W and link/other | **Done for classification fixtures.** Public lifecycle diagnostic coverage remains **incomplete**. |
| D3 nested active journal and nested marker | **Done by test presence; native filesystem execution blocked locally.** |
| D3 oversized-original preservation and full capture digest | **Done for positive fixtures.** |
| D3 journal cap and positive short reads | **Done in part.** Total-consumption assertion is missing. |
| D3 tail-only authentication negative | **Wrong:** F3. |
| D3 cuts with short digest reads | **Missing.** |
| E2 refusal → lifecycle/broker → worker → launcher → parser → public result | **Wrong/incomplete:** F4. |
| E2 healthy/degraded cleanup, legacy and malformed terminals | **Done for helper-level controls; missing complete transport coverage.** |
| E2 refusal-witness public oracle, snapshot and zero-spawn pairing | **Missing.** |
| E1/E3 shipped procedure, README and guidance links | **Done for presence.** Document acceptance coverage is partial. |
| E1/E3 restoration/quarantine scenarios and external model transitions | **Missing in part:** F5. |
| Exact snapshot dedup retains completed names/bytes/kinds | **Wrong for kinds:** F1. |
| Fixed-point consumer equivalence and closure proof | **Wrong/incomplete:** F1, F5. |
| Original producer → X → Y cut exploration | **Done.** Depth-two test passed locally; that does not prove the missing extensions. |
| Every specified regression and mutation-sensitive oracle extension | **Missing/incomplete.** |
| Packaged lock regeneration/check | **Done as an artifact.** Current `--check` passes; regeneration is reported. |
| Baseline-relative fast tier | **Done according to supplied evidence:** 6,042 tests, two baseline failures, zero new failures. |
| `ROUND 3 FIXES` report | **Done for presence; completion claims exceed demonstrated coverage.** |

## GATE GAP

The supplied gate can pass while completed artifact kinds with opposite continuations are deduplicated, recovery of a derived active journal breaks after repeated same-caller deaths, oversized-marker tail corruption is accepted, or worker start drops storage diagnostics.

The new regressions demonstrably miss these boundaries. The depth-two test also no longer proves fixed-point membership.

## PREMISE

- The supplied gate result is baseline-relative PASS; the two existing failures are not new batch defects.
- Round 3 preserves the previous production changes.
- `DIFF.patch` still omits README and `docs/STORAGE_RECOVERY.md`; their live contents were inspected separately.
- Present production fixes do not establish completion of the binding regression/model requirements.

## NOT VERIFIED

- Native temporary-directory and writable-lock tests could not complete in the read-only sandbox. Their environment errors are not product failures.
- Seventeen focused memory/document tests, all four migrated envelopes, the size check and packaged-lock check passed locally. The depth-two test passed during the full model-module attempt.
- D1, D3 and operator test bodies were additionally exercised with virtual temporary paths; this does not establish native filesystem behavior.
- The complete fast tier was not rerun; its result is orchestrator-supplied evidence.
- No files, commits or handoff artifacts were written. No Git, DayZ, daemon, MCP, installer, pip or deployment operation was performed.

