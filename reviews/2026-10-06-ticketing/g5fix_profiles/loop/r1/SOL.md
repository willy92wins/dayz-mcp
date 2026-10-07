VERDICT: CHANGES_REQUIRED

## FINDINGS

### F1 — P2: log readers do not validate the recorded project root

**Locations:** `tools/dayz_mcp/launch_logs.py:140`, `tools/dayz_mcp/log_tail.py:209`, `tools/dayz_mcp/server.py:2408`, `tools/dayz_mcp/server.py:5403`.

Admission uses `log_tail.is_allowed_profiles_dir(item)`, which checks the selected leaf and role-parent name, but never checks the sealed project root. The project-aware validation added to `dayz_test_tool.py` does not reach these consumers.

**Executable scenario:**

- Bind instance `130`; approved project root is `C:\approved`.
- Lifecycle returns a live `@ExampleMod` run anchored at `C:\unapproved\_server\profiles-130`, with a valid launch timestamp.
- Its fresh `script.log` contains `FOREIGN_READY\n`.
- Call `logs_since(run_id=R)` and `wait_for(condition="log_matches", pattern="FOREIGN_READY", lookback_from="launch")`.

**Wrong outputs:** `logs_since` returns the foreign file and sentinel; `log_matches` returns `satisfied: true`.

I reproduced both through the real readers, mocking filesystem I/O only. Meanwhile, `_validated_recorded_leaf(approved_policy, run)` correctly returned `None`.

**Fix:** validate each complete recorded anchor against its sealed project and bound instance before sibling discovery. Feed that validation into both log readers and capture-window resolution.

### F2 — P2: traversal rejection happens after traversal has been erased

**Location:** `tools/dayz_mcp/dayz_test_tool.py:821`.

The implementation performs:

```python
normalized = ntpath.normpath(profiles)
if any(part == ".." for part in normalized.split("\\")):
```

Normalization removes ordinary `..` components before the check.

**Executable scenario:** bind instance `130`, approve `C:\approved`, and supply a client run with:

```text
profiles = C:\approved\_server\..\_client\profiles-130
```

**Wrong outputs:** `_validated_recorded_leaf` returns `profiles-130`; `_profiles_dir_for_role(run, "client")` returns `C:\approved\_client\profiles-130`. The binding specification requires rejection of the traversal-bearing anchor before role resolution.

Both outputs were reproduced without filesystem mutation. This demonstrates invalid-anchor acceptance; an escape outside the approved project was **not** demonstrated.

**Fix:** reject traversal components in the original path before normalization.

### F3 — P2: client extension proceeds after recorded-anchor validation fails

**Locations:** `tools/dayz_mcp/dayz_test_tool.py:874`, `tools/dayz_mcp/dayz_test_tool.py:2857`, `tools/dayz_mcp/dayz_test_tool.py:2868`. The admission gap is at `tools/dayz_mcp/dayz_test_tool.py:602`.

`require_extension_run` checks state and mod name only. The new root resolver turns an invalid recorded anchor into `[]`, and the caller proceeds:

```python
artifacts_paths=_artifact_paths(policy, mode),
```

This reconstructs planned artifacts instead of rejecting the invalid existing run.

**Executable scenario:**

- Bind instance `130`; approve `ExampleMod` at `C:\approved`.
- Supply an otherwise eligible `RUNNING_IDLE` server run with UUID `12345678-1234-4234-8234-1234567890ab`, mod `@ExampleMod`, and profiles `C:\unapproved\_server\profiles-130`.
- Call `execute_dayz_test_run(project="ExampleMod", mode="client", run_id=R)` with successful admission fixtures and a mocked launch consumer.

**Wrong behavior:** the launch consumer is called once, with:

```text
client_dump_roots = []
artifacts_paths = [C:\approved\_client\profiles-130]
```

The recorded-anchor validator returns `None`, yet execution is admitted. I reproduced this at the public adapter boundary. Actual spawning was mocked; the experiment proves the missing rejection, not a successful real launch.

**Fix:** require valid recorded-anchor resolution before extension or replacement admission, dump-baseline construction, and artifact reporting. Invalid anchors must fail rather than silently disable diagnosis.

## SPEC COVERAGE

“Done” below means implemented in inspected source. Fixture-based execution remains supported by the orchestrator’s supplied gate, except where noted.

| Specification requirement | Assessment |
|---|---|
| Provision during admitted `ServerState.prepare`, before seeding | **done** |
| No installer/manual named-folder prerequisite; no recursive project provisioning | **done** |
| Scope excludes bridge, storage-format, ownership and shutdown redesign | **done** |
| Explicit pure helper; omission, valid token and malformed-token contract | **done** — `server_cli.py:46` |
| Helper used by worker, attestation, artifacts/client roots, VPP and preparation | **done** |
| Worker/VPP token from canonical request; preparation/readers from bound context; no environment inference | **done** |
| Preserve absolute-path and owner-name rejection | **done** |
| Require existing project and appropriate role roots | **done** |
| Check leaf and seeding credentials before creating an absent leaf | **done** |
| Create only the missing leaf; reuse ordinary directories | **done** |
| Reject file leaves and escaping redirections; recheck concurrent creation | **done** |
| Preserve atomic seeding, endpoint/key checks, UUID rewrite/reread and preparation-before-spawn ordering | **done** |
| Preserve typed preparation failures and endpoint/owner errors | **done** through existing lifecycle error mapping |
| No migration, copying, foreign-config overwrite or profile-directory cleanup deletion | **done** |
| Planned paths use project/mode policy and selected token | **done** |
| Existing-run resolution validates complete anchor before role selection | **wrong** — F1–F3 |
| Reject wrong project/token, named-context legacy anchors, relative paths and traversal | **wrong** — traversal and some callers remain defective |
| Retain mode authority, `_start_role_roots` and server-anchor stop expansion | **done** |
| Named log admission and sibling-leaf preservation | **done** for token selection; **wrong** for project admission |
| Preserve cursors, scan bounds, launch filtering, deadlines and aggregate `log_matches` behavior | **done** in inspected code |
| `file_matches` role selection and client/offline eligibility | **done** for valid anchors; **wrong** traversal admission |
| Named client path reaches capture disambiguation | **done** by source trace; **wrong** shared project validation |
| Named close watches and server logout reads; unchanged freshness, rotation, retirement and close order | **done** |
| Explicit attestation token through boundaries and all worker initialization calls | **done** |
| Preserve missing/unreadable boundaries and complete-line requirements | **done** in unchanged readers |
| VPP named paths; unchanged `serverDZ.cfg`; read-only administrative preflight | **done** |
| Named stalled-start and bounded dump roots | **done** for valid runs; **wrong** invalid-extension handling |
| Default names, ordering, argv construction and established config serialization | **done** by source comparison; exact byte-regression coverage incomplete |
| Missing default profile directories still fail | **done** |
| Reviewed lock hashes | **done** — independent checks passed |
| Packaged import closure and builder/verifier module-set parity | **done** — three independent tests passed |
| Rebuilt/resealed launcher and bundle verification | **missing** from review evidence; assignment conflict described below |
| Root README provisioning/conflict/VPP guidance | **done** in this tree |
| MCP README artifact locations and role distinction | **done** |
| Run-manifest/intent formats, recovery and foreign-process redaction unchanged | **done** |
| Subsequent in-game confirmation | **missing**, intentionally assigned to the orchestrator |

Required regression groups:

| Group | Assessment |
|---|---|
| Fresh named preparation | **done** direct preparation test; **missing** mocked spawn-order assertion |
| Fail-closed preparation | **done** several negatives; **missing** separate existing-project/missing-role-root and concurrent non-directory cases |
| Mode/path parity | **done** helper/path assertions; **missing** launch-consumer and actual recorded-anchor assertions |
| Default compatibility | **done** name/path and UUID checks; **missing** exact argv/config-byte comparison |
| Log admission/siblings | **done** both directions and token isolation; **missing** foreign-project isolation |
| `logs_since` / `log_matches` | **done** cursor, launch and lookback examples; **wrong** foreign-project admission |
| `file_matches` | **done** named client/offline positives and legacy-anchor rejection; **missing** named server positive and full invalid-anchor matrix |
| Close/logout | **done** public close metrics, linked logout and deadline warning fixtures |
| RPT rotation | **done** named rotation, legacy isolation and stale termination fixtures; **missing** other-token rotation case |
| Stop artifacts | **done** resolver assertions; **missing** public stop-result assertion |
| Client diagnosis | **done** named roots, fresh dumps and ceiling examples; **missing** invalid-extension refusal |
| Attestation | **done** named sources and pre-boundary case; **missing** legacy-only post-boundary negative and worker-level boundary threading tests |
| VPP | **done** named-only versus legacy-only state and unchanged server-config path |
| Existing correct consumers | **done** recorded-config recovery and redacted foreign label |
| Packaging | **done** hashes/closure/parity; **missing** resealed-bundle verification |

## GATE GAP

The gate can pass with all three findings:

- New log tests distinguish profile leaves, but never challenge the approved **project root**.
- Invalid-anchor coverage does not include traversal or malformed existing-run extension.
- Mode tests call `_start_core`; they do not prove admission, launch consumption and manifest recording together.
- The attestation test introduces matching named and legacy evidence together, so it does not establish the required legacy-only negative.
- Hash-lock tests verify source hashes, not a newly built launcher.

Independent non-writing checks passed: **seven tests** covering the helper, lock hashes/set, import closure and builder/verifier parity.

The fixture-suite attempt ran **33 tests, with 27 environment errors and two skips**, because this read-only sandbox cannot create temporary directories. Those errors are not implementation failures.

## PREMISE

- **`REPORT.md` is absent.** The implementer’s report could not be reviewed.
- **Reseal instructions conflict.** `C:\Users\guill\dzmcp_gauntlet\g5fix_profiles\brief_r1.md:4` explicitly prohibits implementer resealing and assigns it to deployment; its embedded specification requires resealing for acceptance. This needs an explicit acceptance-stage resolution. An old bundle’s source pins already fail against this tree with `invalid_native_launcher_bundle__dayz_test_worker_sha256`, independently reproduced.
- **The supplied patch omits the root README change present in this tree.** Therefore, applying `DIFF.patch` alone does not reproduce every reviewed change.
- The injected October 1 LIVE-STATE is older than this batch and was not treated as its current baseline.

## NOT VERIFIED

- Real filesystem provisioning/concurrency experiments under this sandbox.
- Full fixture suites, Python 3.11 execution, or the complete fast tier independently.
- Rebuilt launcher artifacts, receipts or installed bundle verification.
- In-game role paths, logout observation and fresh termination evidence.
- Actual data loss or real process spawning.

No source files were modified; no DayZ, daemon or MCP lifecycle operations were started.

