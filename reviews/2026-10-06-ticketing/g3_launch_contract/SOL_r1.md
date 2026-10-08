# gpt-6.1-sol review, g3_launch_contract, round 1 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Paths below refer to the modified tree. Read-only probes reproduced the failure scenarios; they did not contact DayZ or the daemon.

**F1 — P1 — Stale profile files can pass initialization attestation.**  
`tools/dayz_mcp/dayz_test_attestation.py:329`, `:365`; `tools/dayz_mcp/dayz_test_worker.py:977`

- **Scenario:** Enable an initialization requirement with `role="server"`, `source="profile_file"`, `filename="ready.txt"`, and `pattern="READY"`. Before launch, `ready.txt` already contains `READY\n`; the launched process writes nothing.
- **Wrong output:** `capture_log_boundaries()` excludes `ready.txt`, so its boundary defaults to zero. Initialization returns `passed`. The probe reproduced `STALE_PROFILE_FILE {} passed`.
- Boundaries also lack partial-line state: an existing `OLD ` followed after the boundary by `READY\n` is accepted as a complete new matching line.
- **Fix:** Capture each applicable profile filename and record whether the boundary intersects an unfinished line. Accept only lines beginning and completing after that boundary.

**F2 — P1 — All-mode captures the client boundary before the client starts.**  
`tools/dayz_mcp/dayz_test_worker.py:975`, `:1036`, `:1042`, `:1055`

- **Scenario:** Run `mode="all"` with a client initialization rule. After the server starts, an existing client writes `READY\n` during the server readiness wait. The subsequently launched client writes no initialization marker.
- **Wrong output:** The earlier write satisfies the client rule because both role boundaries were captured before starting the server. A worker probe returned `attestation.status="passed"` with that exact sequence.
- **Fix:** Capture each role’s evidence immediately before its own lifecycle start, including after the server readiness wait for the client.

**F3 — P2 — Preflight succeeds without accrediting selected filesystem paths.**  
`tools/dayz_mcp/dayz_test_tool.py:2249`, `:2262`, `:2331`; `tools/dayz_mcp/dayz_test_worker.py:468`

- **Scenario:** A sealed project permits `C:\review_absent_31d2\mods` and `...\missions`. Call:
  `project="ExampleMod", mode="server", preflight=true, base_mods=["@Missing"], extra_mods=["@DayZ_MCP"], mission="C:\review_absent_31d2\missions\missing"`.
  Those directories do not exist.
- **Wrong output:** The actual `VerifiedNativeBundle` runtime accessor and host branch return `status="succeeded", error_code=null`. `assess_preflight()` resolves strings but checks neither mod-directory existence nor filesystem identities. The branch never invokes the existing accreditation mechanism.
- This affects production bundles, despite `REPORT.md:57` describing the omission as specific to test doubles.
- **Fix:** Accredit sealed roots, source, absolute missions, and every effective mod directory—including the implicit project directory—without acquiring a lease.

**F4 — P2 — Runtime-policy validation errors are swallowed into successful preflight.**  
`tools/dayz_mcp/dayz_test_tool.py:2253`, `:2257`, `:2284`, `:2331`

- **Scenario:** The verified runtime document has no matching project, for example `{"format_version":1,"projects":[]}`. Preflight an otherwise syntactically valid server request.
- **Wrong output:** `validated_worker_runtime()` raises, but the adapter converts that error to `runtime_policy=None` and returns success. The probe reproduced `MALFORMED_RUNTIME_PREFLIGHT succeeded None`.
- With enabled attestation and a future build, that fallback can also omit the required pending attestation report entirely.
- **Fix:** Require the runtime accessor and propagate validation failure. Remove the production fallback used to accommodate incomplete test doubles.

**F5 — P2 — Override rejects distinct candidate directories by basename.**  
`tools/dayz_mcp/dayz_test_request.py:488`, `:495`

- **Scenario:** Logical project `ExampleMod` has original directory `P:\Mods\@ExampleMod`; policy also permits `C:\candidate`. Request `project_mod_override=true`, `base_mods=["C:\candidate\@ExampleMod"]`, and `extra_mods=["@DayZ_MCP"]`.
- **Wrong output:** `bad_dayz_test_request:project_mod_override_includes_original`, although the candidate and original are different normalized paths.
- **Fix:** Compare resolved runtime paths. A matching basename alone must not reject a candidate.

**F6 — P2 — Artifact attestation omits loaded server-only mods.**  
`tools/dayz_mcp/dayz_test_worker.py:292`, `:347`, `:450`

- **Scenario:** Launch server mode with `server_mods=["@ServerOnly"]`. Its deployed `Addons\ServerOnly.pbo` is the sole matching artifact and contains every required entry.
- **Wrong output:** The server argv includes `-serverMod=P:\Mods\@ServerOnly`, but artifact resolution searches only base/project/extra directories and reports artifact missing.
- **Fix:** Make artifact resolution reflect the directories loaded by the requested roles, including `server_mods` for server/all launches.

**F7 — P2 — Occupancy sampling reports busy boxes as free.**  
`tools/dayz_mcp/dayz_test_tool.py:2062`, `:2074`, `:2078`

- **Scenario:** Lifecycle status contains one managed run with `state="STARTING"` and a known run ID.
- **Wrong output:** `_sample_box_occupancy()` returns `(False, None)`. The probe reproduced this result. The lifecycle retains STARTING/STOPPING states, and its box observer also handles foreign DayZ processes; this implementation ignores both dimensions.
- **Fix:** Sample authoritative box occupancy through a read-only route. Preserve uncertainty as `null`; return the known managed occupant when available.

**F8 — P2 — Reported PBO hash can describe different bytes from those verified.**  
`tools/dayz_mcp/dayz_test_attestation.py:290`, `:291`, `:310`

- **Scenario:** Plain PBO A contains only `config.bin`. After `_sha256_file()` closes its handle, replace the pathname with plain PBO B containing the required script entry, before the second open.
- **Wrong output:** Artifact status is `passed`, but its SHA256 is A’s hash while the verified entries came from B. A two-read in-memory probe reproduced this mismatch.
- **Fix:** Hash and parse the same bounded payload from one read. Retain a measured digest when subsequent parsing is unverifiable.

**F9 — P2 — Missing evidence masks an unverifiable evidence failure.**  
`tools/dayz_mcp/dayz_test_attestation.py:448`, `:455`; `tools/dayz_mcp/dayz_test_worker.py:567`

- **Scenario:** Two applicable initialization rules: one has no marker; the other is unreadable or exceeds the scan limit.
- **Wrong output:** Aggregate status becomes `failed`; the worker waits until timeout and returns `project_attestation_initialization_missing`, despite an `unverifiable` row. The worker probe reproduced this error.
- **Fix:** Preserve unverifiable evidence as a distinct failure reason instead of allowing an ordinary missing marker to override it.

**F10 — P3 — Attestation policy validation is not strictly typed.**  
`tools/dayz_mcp/dayz_test_attestation.py:90`, `:120`, `:151`

- **Scenario:** Policies with `version=true` or `version=1.0` are accepted. An entry list containing `{}`, or an initialization `source={}`, escapes validation as `TypeError`.
- **Wrong output:** Invalid versions are enabled; other invalid types bypass the intended policy-validation error handling.
- **Fix:** Validate types before equality or set-membership operations and return the closed policy error consistently.

**F11 — P3 — Required acceptance-contract documentation is missing.**  
`product-spec.md:146`, `:148`; `REPORT.md:57`

H11/H13 were deliberately left unchanged. The tool description and changelog do not complete this explicitly requested documentation work.

## SPEC COVERAGE

“Done” describes inspected source behavior, not live deployment certification.

### c561

| Specification clause | Status |
|---|---|
| Optional policy member; disabled by default | done |
| Carry policy through request, builder, sealed loader, bootstrap | done |
| Package the new helper | done |
| Version, counts, IDs, literal patterns, sources and filenames | wrong — strict type validation incomplete, F10 |
| Finite positive timeout, default 60, maximum 300 | done |
| Reject unknown keys, duplicate IDs, lexical traversal and absolute paths | done |
| Apply rules by requested role; report other roles as `not_applicable` | done |
| Disabled policy preserves existing launch behavior and terminal fields | done |
| Check artifacts after build and before process start | done |
| Resolve deployed artifacts against the effective set, including override | wrong — server-only mods omitted, F6 |
| Fail closed on zero/multiple artifact matches | done |
| Verify explicit entries without inferred script requirements | done |
| Record deployed SHA256 | wrong — hash and verification can disagree, F8 |
| Unsupported encoding is unverifiable; archive reads bounded | done |
| Capture evidence immediately before each applicable role starts | wrong — F1, F2 |
| Accept only complete post-boundary lines | wrong — F1 |
| Track new logs and appends rather than selecting the newest old log | done |
| Poll within the admitted worker transaction | done |
| Deadline without matching evidence fails | done |
| Unreadable/over-limit evidence has a distinct unverifiable failure | wrong — mixed failures mask it, F9 |
| Initialization failure cleans newly created runs through lifecycle | done |
| Reattach preserves server and identifies the attempted supplied run | done for initialization failures |
| Bounded report: aggregate status, IDs, statuses, hashes; no raw logs | done structurally; wrong freshness/hash claims, F1/F2/F8 |
| Required worker error codes | done |
| Preserve launch error and cleanup-degraded independently | done |
| Preflight initialization pending; future-build artifacts pending | done with valid runtime; wrong fallback, F4 |
| Carry report through worker/error/terminal/public projection | done for attestation success/failure; missing on unrelated launch errors/cancellation |
| Legacy terminal support; closed additional keys; 4096-byte consumer limit | done |
| Description and changelog distinguish launch, attestation and acceptance | done |
| Update H11/H13 | missing — F11 |
| Existing callers/policies remain opt-out | done |
| Enabled policy requires combined reseal and coordinated deployment | done in source/documentation; deployment not verified |
| No PBO bridge or persistence-format migration | done |
| Required regression/control coverage | wrong — major cases absent; see GATE GAP |

### 31d2

| Specification clause | Status |
|---|---|
| Dedicated branch before FIFO, takeover and idle-session reconciliation | done |
| Held lease is not released, acquired or heartbeated by this branch | done in inspected call path |
| Shared validator with worker preflight | done structurally; incomplete checks, F3/F4 |
| Expose already verified runtime without reopening JSON | done |
| Closed request grammar and mode/run-ID matrix | done |
| Verify sealed executable, bundle, policy and existing source pins | done |
| Accredit path-root identities and actual effective mod directories | missing — F3 |
| Validate runtime aliases and accredit absolute missions | wrong — F3/F4 |
| Source/build-policy constraints without scanning/building assets | done with valid runtime; fail-open fallback, F4 |
| VPP requirements | done |
| Read-only Steam check for client-starting modes | done |
| Preserve applicable desktop check | done |
| Remediation flag never authorizes Steam repair | done |
| Do not execute the secure launcher or launch the executable | done |
| Compact envelope plus `preflight=true` | done |
| Correct advisory `box_busy` and exact known occupant | wrong — F7 |
| Preserve supplied reattach ID; mint no ID | done |
| Remove Steam omission; disclose live checks and pending initialization | done |
| Busy box cannot mask validation failures | wrong — filesystem/runtime failures are missed, F3/F4 |
| Queue/takeover options validated but never executed | done |
| Description/changelog: no free-box or future-launch guarantee | done |
| Update H11/H13 | missing — F11 |
| Additive compatibility; legacy callers receive real Steam refusal | done |
| Reseal required; no storage rotation or protocol migration | done in source; reseal unverified |
| Required positive/negative regression coverage | wrong — production validator path is bypassed in fixtures |

### 2837

| Specification clause | Status |
|---|---|
| Public StrictBool and adapter/request forwarding | done |
| Existing v1 grammar/keysets and historical canonical requests | done |
| Default-false calls emit v1; override calls emit v2 | done |
| Closed v1/v2 parsing; v1 rejects override key | done |
| Default composition and storage fingerprint unchanged | done |
| Explicit non-bridge candidate declaration required | done |
| Omit only implicit project entry; retain order/server semantics | done |
| Reject original directory by actual normalized runtime path | wrong — basename rejection, F5 |
| Reject build/clean before side effects | done |
| Worker and VPP composition agree | done |
| Preflight inspects actual effective directories | missing — F3 |
| Effective bridge gate and explicit-extra bridge rule | done |
| Preserve bridge-default reporting | done |
| Null implicit-project storage role; candidate remains base/extra | done |
| Existing recoverable rotation on new server/offline launch | done in source |
| No rotation on preflight/reattach | done |
| Same-path byte changes remain outside storage-seal assurance | done |
| Logical identity retained for labels, stop and extension matching | done |
| `project_mod_replaced=true`, including preflight | done |
| Closed rejection reasons and public translation | done; original-path reason over-applied, F5 |
| Description/changelog; no automatic prefix-collision detection | done |
| Update H11/H13 | missing — F11 |
| Reseal, old-parser refusal and existing marker rollback behavior | done in source; operational checks unverified |
| Required regression/negative coverage | wrong — distinct same-basename candidates not tested |

## GATE GAP

The supplied PASS does not cover the reproduced defects:

- Freshness tests exercise `script.log` append offsets, not persisted non-log profile files, boundary-crossing lines, or client evidence produced during server readiness.
- Preflight fixtures use `_Bundle`, which has no runtime accessor. Most successful tests therefore exercise the fallback that skips the shared runtime validator (`tools/tests/test_launch_contract_c561_31d2_2837.py:515`).
- The host/worker agreement test calls `assess_preflight()` directly as its “host” side, rather than exercising host accreditation (`:664`).
- Artifact tests use stable reads and base/extra candidates; they miss replacement between hashing and parsing and artifacts loaded through `server_mods`.
- Occupancy fixtures use RUNNING rows, missing transitional states and foreign processes.
- Original-directory tests check matching leaves, without a valid distinct candidate bearing the same basename.
- Single-rule evidence errors miss the mixed missing/unverifiable precedence bug.
- Terminal tests do not ensure enabled-policy reports survive ordinary launch failures or cancellation.

## PREMISE

The batch grouping is appropriate. The supplied gate establishes no new failures under its measured fixtures; it does not establish specification compliance.

The earlier “exactly five keys” premise is outdated by the approved c8c7 terminal context fields. Preserving those fields alongside attestation is appropriate.

`REPORT.md:57` asserts an editing boundary excluding `product-spec.md`; the supplied review specification explicitly requires H11/H13 changes. That documentation omission still needs integration.

The October 1 live-state snapshot proves nothing about this modified tree’s deployment. A combined reseal remains an integration requirement.

## NOT VERIFIED

- Default Python 3.14 could not import the test module because `anyio` was absent.
- With `C:\Users\guill\dzmcp_gauntlet\venv311\Scripts\python.exe -B`, the module ran 21 tests: seven errored because the read-only sandbox prohibited temporary-directory creation. The remaining 14 were rerun separately and passed.
- The supplied fast-tier and Python 3.11 gate results were not independently reproduced in full.
- Adversarial probes used in-memory I/O and lifecycle fixtures. Real Windows races, containment defenses, heartbeat supervision and cancellation cleanup remain unverified.
- No launcher reseal/registration, deployed PBO inspection, foreign-box live check, in-game argv inspection or storage-rotation experiment was performed.
- No DayZ process, daemon or MCP endpoint was contacted. No files were edited, and no commit or handoff file was written.

