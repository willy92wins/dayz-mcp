VERDICT: FINDINGS

## MECHANICAL

Read-only grep and table walks completed. No code, tests, processes, or MCP tools were executed.

Notation: **W** world; **D** world backup; **M** canonical marker; **K** marker backup; **J** active journal; **C** completed journal; **T** temporary. **P/S/Q** are `prepared` / `storage_moved` / `marker_published`; **E** original marker bytes; **N** journal’s canonical marker; **X** current caller’s marker.

### 1. Path-naming matrix

| Artifact / naming authority | Producer | Recovery | Cleanup / retirement | Other reader | Result and evidence |
|---|---|---|---|---|---|
| W: `storage_1` | `rotate_storage` joins `STORAGE_NAME` | `_observe_pair` joins the same constant | Renamed W→D | Lifecycle joins the same constant | Names agree. `tools/dayz_mcp/dayz_test_storage.py:49`, `tools/dayz_mcp/dayz_test_storage.py:843`, `tools/dayz_mcp/dayz_test_storage.py:1034`, `tools/dayz_mcp/dayz_test_storage.py:1075`; `tools/dayz_mcp/process_lifecycle.py:3311`. |
| D: `storage_1.modset-<stamp>-<old-seal8 or legacy>` | Inline template; `_backup_stamp` supplies timestamp | Joins validated journal `storage_backup` | Retained | Lifecycle joins journal `storage_backup` | Producer and readers agree; recovery intentionally accepts other plain reserved names. `tools/dayz_mcp/dayz_test_storage.py:397`, `tools/dayz_mcp/dayz_test_storage.py:474`, `tools/dayz_mcp/dayz_test_storage.py:844`, `tools/dayz_mcp/dayz_test_storage.py:1051`; `tools/dayz_mcp/process_lifecycle.py:169`. |
| M: `storage_1.modset.json` | `_publish_marker` joins `MARKER_NAME` | `_observe_pair`, `_apply_action`, `_finish_recorded` use that constant | Original renamed M→K; publication replaces M | `read_marker` | Names agree. `tools/dayz_mcp/dayz_test_storage.py:50`, `tools/dayz_mcp/dayz_test_storage.py:368`, `tools/dayz_mcp/dayz_test_storage.py:599`, `tools/dayz_mcp/dayz_test_storage.py:845`, `tools/dayz_mcp/dayz_test_storage.py:875`, `tools/dayz_mcp/dayz_test_storage.py:932`. |
| K: `<D>.marker.json` | Inline template; recorded in J | Joins validated journal `marker_backup` | Retained | Preservation predicates read its bytes; lifecycle reports its name | Names agree. `tools/dayz_mcp/dayz_test_storage.py:475`, `tools/dayz_mcp/dayz_test_storage.py:681`, `tools/dayz_mcp/dayz_test_storage.py:846`, `tools/dayz_mcp/dayz_test_storage.py:877`, `tools/dayz_mcp/dayz_test_storage.py:1053`; `tools/dayz_mcp/process_lifecycle.py:180`. |
| J: `storage_1.modset.rotation.<txid>.json` | Inline prefix/id/suffix construction | `_reconcile_journal` independently constructs it; scanner uses a literal regex | `_complete_journal` independently constructs source name | `_valid_journal` independently constructs reserved name | **M4:** repeated construction without a shared naming helper. **M1:** scanner ignores a differently cased prefix. `tools/dayz_mcp/dayz_test_storage.py:52`, `tools/dayz_mcp/dayz_test_storage.py:55`, `tools/dayz_mcp/dayz_test_storage.py:485`, `tools/dayz_mcp/dayz_test_storage.py:531`, `tools/dayz_mcp/dayz_test_storage.py:984`, `tools/dayz_mcp/dayz_test_storage.py:1010`, `tools/dayz_mcp/dayz_test_storage.py:1054`. |
| C: `storage_1.modset.rotation.<txid>.completed.json` | `_complete_journal` constructs destination | Validator, observation and reservation independently construct it | Retained; active scanner excludes completed suffix | Lifecycle has its own literal regex | Current canonical spellings agree. **M4:** duplicated naming grammar. `tools/dayz_mcp/dayz_test_storage.py:54`, `tools/dayz_mcp/dayz_test_storage.py:486`, `tools/dayz_mcp/dayz_test_storage.py:532`, `tools/dayz_mcp/dayz_test_storage.py:849`, `tools/dayz_mcp/dayz_test_storage.py:1012`, `tools/dayz_mcp/dayz_test_storage.py:1059`; `tools/dayz_mcp/process_lifecycle.py:131`. |
| T: `storage_1.modset.tmp-<pid>-<sequence>` | `_exclusive_temporary`; both atomic publishers call it | Never used as recovery evidence | Rename/replace consumes it; handled publication errors unlink it | Scanner ignores it because it lacks `JOURNAL_PREFIX` | Names and exclusion agree. `tools/dayz_mcp/dayz_test_storage.py:311`, `tools/dayz_mcp/dayz_test_storage.py:335`, `tools/dayz_mcp/dayz_test_storage.py:342`, `tools/dayz_mcp/dayz_test_storage.py:350`, `tools/dayz_mcp/dayz_test_storage.py:576`, `tools/dayz_mcp/dayz_test_storage.py:591`, `tools/dayz_mcp/dayz_test_storage.py:1010`. |

### 2. Cleanup symmetry

| Artifact | Successful path | Refusal / handled failure | Crash-cut continuation | Result and evidence |
|---|---|---|---|---|
| W | Rotation renames W→D; intact prepared recovery retains W | Physical-state refusal retains it | Before rename: intact P aborts. After rename: D holds original | Symmetric for admitted states. Ordinary classification has the type gap **M2**. `tools/dayz_mcp/dayz_test_storage.py:649`, `tools/dayz_mcp/dayz_test_storage.py:702`, `tools/dayz_mcp/dayz_test_storage.py:869`, `tools/dayz_mcp/dayz_test_storage.py:1075`, `tools/dayz_mcp/dayz_test_storage.py:1154`. |
| D | Retained as original world | Retained; no rollback/delete | Every moved-world continuation requires D to remain a directory | Deliberate retention, not orphan cleanup failure. `tools/dayz_mcp/dayz_test_storage.py:15`, `tools/dayz_mcp/dayz_test_storage.py:649`, `tools/dayz_mcp/dayz_test_storage.py:656`, `tools/dayz_mcp/dayz_test_storage.py:1075`. |
| Original M / E | Existing original renamed into K before N publication | Refusal retains it; handled errors retain already committed moves | Before preservation: E in M. After preservation: E in K | Symmetric through recognized J states; skipped alias J can bypass preservation (**M1**). `tools/dayz_mcp/dayz_test_storage.py:703`, `tools/dayz_mcp/dayz_test_storage.py:719`, `tools/dayz_mcp/dayz_test_storage.py:874`, `tools/dayz_mcp/dayz_test_storage.py:1010`, `tools/dayz_mcp/dayz_test_storage.py:1170`. |
| Published M / N / X | Retained as current canonical marker | Published bytes are retained after subsequent failure | Before replace: prior state. After replace: S5 or resealed S8 | Recovery understands publication ahead of phase. `tools/dayz_mcp/dayz_test_storage.py:575`, `tools/dayz_mcp/dayz_test_storage.py:714`, `tools/dayz_mcp/dayz_test_storage.py:725`, `tools/dayz_mcp/dayz_test_storage.py:943`. |
| K | Retained; never overwritten | Retained | S4 onward validates original hash; absent-original branch requires no K | Symmetric, including E=N. `tools/dayz_mcp/dayz_test_storage.py:681`, `tools/dayz_mcp/dayz_test_storage.py:711`, `tools/dayz_mcp/dayz_test_storage.py:719`, `tools/dayz_mcp/dayz_test_storage.py:730`, `tools/dayz_mcp/dayz_test_storage.py:875`. |
| J | Renamed to C(P) on abort or C(Q) on completion | Refusal retains J; recoverable I/O failure retains last published phase | P→S→Q→C; cuts leave a predecessor/successor state | Canonical J retires. Differently cased-prefix J is neither recovered nor retired (**M1**). `tools/dayz_mcp/dayz_test_storage.py:529`, `tools/dayz_mcp/dayz_test_storage.py:869`, `tools/dayz_mcp/dayz_test_storage.py:872`, `tools/dayz_mcp/dayz_test_storage.py:885`, `tools/dayz_mcp/dayz_test_storage.py:971`, `tools/dayz_mcp/dayz_test_storage.py:1010`. |
| C | Retained as history | Retained | After completion, storage classifies normally; lifecycle may replay pending reset evidence | Retention is specified. Lifecycle loses that evidence after A→X resealing (**M3**). `tools/dayz_mcp/dayz_test_storage.py:532`, `tools/dayz_mcp/dayz_test_storage.py:1012`; `tools/dayz_mcp/process_lifecycle.py:165`, `tools/dayz_mcp/process_lifecycle.py:3303`; `_audit_context/SPEC_R9B_IMPL.md:125`. |
| T | Consumed by publication | Existing T is unlinked where publication cleanup runs; failed cleanup may retain it | Empty/partial/full T survives abrupt death; recovery ignores it | No later reader treats leftover T as live transaction evidence. `tools/dayz_mcp/dayz_test_storage.py:319`, `tools/dayz_mcp/dayz_test_storage.py:342`, `tools/dayz_mcp/dayz_test_storage.py:347`, `tools/dayz_mcp/dayz_test_storage.py:583`, `tools/dayz_mcp/dayz_test_storage.py:588`, `tools/dayz_mcp/dayz_test_storage.py:1010`; `_audit_context/SPEC_R9B_IMPL.md:132`. |

### 3. Entry points and gates

| Entry / caller | Reads or writes | Validation before mutation | Result and evidence |
|---|---|---|---|
| Lifecycle `start_run` → reserved launch → `_rotate_storage_for_launch` | Calls `prepare_storage`; persists observation before spawn | Lease/reservation, request, ownership/occupancy and final port checks precede rotation | Ordering present. `tools/dayz_mcp/process_lifecycle.py:3757`, `tools/dayz_mcp/process_lifecycle.py:3890`, `tools/dayz_mcp/process_lifecycle.py:3967`, `tools/dayz_mcp/process_lifecycle.py:4208`, `tools/dayz_mcp/process_lifecycle.py:4239`, `tools/dayz_mcp/process_lifecycle.py:4247`, `tools/dayz_mcp/process_lifecycle.py:4310`. |
| `_rotate_storage_for_launch` → `prepare_storage` | Rotation/recovery entry | Checks launch inputs; delegates artifact gate | One production caller found. Inherits **M1/M2**. `tools/dayz_mcp/process_lifecycle.py:3276`, `tools/dayz_mcp/process_lifecycle.py:3290`. |
| `prepare_storage` → `_reconcile_journal` | Active J recovery | Arguments, mission, scan, ambiguity, document validation, then physical selector | Gate present for recognized J; scanner gap **M1**. `tools/dayz_mcp/dayz_test_storage.py:1126`, `tools/dayz_mcp/dayz_test_storage.py:1130`, `tools/dayz_mcp/dayz_test_storage.py:1140`, `tools/dayz_mcp/dayz_test_storage.py:989`. |
| `prepare_storage` → `rotate_storage` | New transaction | Classifies first; producer captures original and reserves destinations before J publication | No other production caller found. `tools/dayz_mcp/dayz_test_storage.py:1161`, `tools/dayz_mcp/dayz_test_storage.py:1033`, `tools/dayz_mcp/dayz_test_storage.py:1037`, `tools/dayz_mcp/dayz_test_storage.py:1061`, `tools/dayz_mcp/dayz_test_storage.py:1074`. |
| `_reconcile_journal` / `rotate_storage` → `_finish_recorded` → `_apply_action` | Recovery transitions | `_observe_pair` and pure selector run before each action | Gate present. `tools/dayz_mcp/dayz_test_storage.py:992`, `tools/dayz_mcp/dayz_test_storage.py:1077`, `tools/dayz_mcp/dayz_test_storage.py:908`, `tools/dayz_mcp/dayz_test_storage.py:919`, `tools/dayz_mcp/dayz_test_storage.py:927`. |
| `prepare_storage` → `_publish_marker`, seal-only branch | Replaces M | Uses boolean “W is directory”; does not distinguish absent from unsupported type | **M2**. `tools/dayz_mcp/dayz_test_storage.py:1154`, `tools/dayz_mcp/dayz_test_storage.py:1169`; `tools/dayz_mcp/dayz_test_storage.py:227`. |
| `_apply_action` / `_finish_recorded` → marker publication, phase advancement, journal completion | Mutates M/K/J/C | Selected action or post-completion checks; strict rename checks destination absence | No independent production shortcut found. `tools/dayz_mcp/dayz_test_storage.py:359`, `tools/dayz_mcp/dayz_test_storage.py:863`, `tools/dayz_mcp/dayz_test_storage.py:930`, `tools/dayz_mcp/dayz_test_storage.py:943`. |
| Lifecycle `_pending_completed_rotation` | Reads C and D; creates reporting result | Validates journal, phase, original seal and backup name/existence; no artifact mutation | Reader gate present, but original-seal filter causes **M3**. `tools/dayz_mcp/process_lifecycle.py:139`, `tools/dayz_mcp/process_lifecycle.py:159`, `tools/dayz_mcp/process_lifecycle.py:165`, `tools/dayz_mcp/process_lifecycle.py:170`, `tools/dayz_mcp/process_lifecycle.py:3315`. |
| Worker launch request builder | Computes seal; does not manipulate rotation artifacts | Adds seal only for newly created server/offline runs | No alternate storage writer. `tools/dayz_mcp/dayz_test_worker.py:536`. |
| `dayz_test_tool` status reader | Reads run record / observation log, not mission files | Unique run/log row, operation identity and payload checks | Reader-only; known operation identity is checked in both stores. `tools/dayz_mcp/dayz_test_tool.py:1966`, `tools/dayz_mcp/dayz_test_tool.py:1975`, `tools/dayz_mcp/dayz_test_tool.py:1999`. |
| Test callers | Call `prepare_storage`, `read_marker`, pending-completed reader | Same public gate; tests also deliberately patch internals | Not production bypasses. `tools/tests/test_dayz_test_storage.py:198`, `tools/tests/test_dayz_test_storage.py:219`, `tools/tests/test_storage_rotation_model.py:498`, `tools/tests/test_storage_reset_visibility.py:561`, `tools/tests/test_storage_reset_visibility.py:569`. |

### 4. Flag and phase lifecycle

| Journal field / related flag | Written | Read / transition | Cleared or retained; store agreement | Evidence |
|---|---|---|---|---|
| `schema_version` | New J uses 2 | Exact schema-1/schema-2 field sets select legacy/current branches | Retained in C; phase advancement does not upgrade legacy | `tools/dayz_mcp/dayz_test_storage.py:428`, `tools/dayz_mcp/dayz_test_storage.py:441`, `tools/dayz_mcp/dayz_test_storage.py:522`, `tools/dayz_mcp/dayz_test_storage.py:658`. |
| `txid` | Recorded in J | Checked against filename; used for C | Retained; post-abort transaction gets derived identity | `tools/dayz_mcp/dayz_test_storage.py:429`, `tools/dayz_mcp/dayz_test_storage.py:467`, `tools/dayz_mcp/dayz_test_storage.py:529`, `tools/dayz_mcp/dayz_test_storage.py:1151`. |
| `phase=P` | Before W moves | Intact→abort; moved→advance S | J→C(P) preserves aborted phase; does not claim rotation | `tools/dayz_mcp/dayz_test_storage.py:1065`, `tools/dayz_mcp/dayz_test_storage.py:1074`, `tools/dayz_mcp/dayz_test_storage.py:702`, `tools/dayz_mcp/dayz_test_storage.py:869`. |
| `phase=S` | After W→D, or lagging-P recovery | Selects preservation/publication; N already present advances Q | Atomically replaced by Q; not a normal completed phase | `tools/dayz_mcp/dayz_test_storage.py:1076`, `tools/dayz_mcp/dayz_test_storage.py:873`, `tools/dayz_mcp/dayz_test_storage.py:710`, `tools/dayz_mcp/dayz_test_storage.py:886`. |
| `phase=Q` | After N publication | Requires N and preservation; completes J | J→C(Q); lifecycle reads Q only | `tools/dayz_mcp/dayz_test_storage.py:730`, `tools/dayz_mcp/dayz_test_storage.py:886`, `tools/dayz_mcp/dayz_test_storage.py:888`; `tools/dayz_mcp/process_lifecycle.py:165`. |
| `new_seal` | Original transaction seal A | Produces/checks N; caller X may subsequently reseal M | A remains in C while M becomes X; lifecycle reader fails to follow this (**M3**) | `tools/dayz_mcp/dayz_test_storage.py:431`, `tools/dayz_mcp/dayz_test_storage.py:610`, `tools/dayz_mcp/dayz_test_storage.py:943`; `tools/dayz_mcp/process_lifecycle.py:166`. |
| `project` | Original transaction project | N uses journal project; different-seal resealing uses caller project | Journal project remains in C; same-seal recovery retains N | `tools/dayz_mcp/dayz_test_storage.py:433`, `tools/dayz_mcp/dayz_test_storage.py:882`, `tools/dayz_mcp/dayz_test_storage.py:945`, `tools/dayz_mcp/dayz_test_storage.py:953`. |
| `old_seal` | From captured original bytes, or null | Schema-2 consistency checks; legacy known-original authentication | Retained in C | `tools/dayz_mcp/dayz_test_storage.py:1047`, `tools/dayz_mcp/dayz_test_storage.py:478`, `tools/dayz_mcp/dayz_test_storage.py:504`, `tools/dayz_mcp/dayz_test_storage.py:815`. |
| `old_marker_state` | `absent`, `present_valid`, `present_invalid` from capture | Determines absence versus required preservation | Retained; absent original requires K absent | `tools/dayz_mcp/dayz_test_storage.py:64`, `tools/dayz_mcp/dayz_test_storage.py:1042`, `tools/dayz_mcp/dayz_test_storage.py:1047`, `tools/dayz_mcp/dayz_test_storage.py:674`, `tools/dayz_mcp/dayz_test_storage.py:711`. |
| `old_marker_sha256` | Hash of the same captured bytes; null only for absence | Authenticates pending M or preserved K | Retained; E=N still requires preserving K when original existed | `tools/dayz_mcp/dayz_test_storage.py:1046`, `tools/dayz_mcp/dayz_test_storage.py:1048`, `tools/dayz_mcp/dayz_test_storage.py:504`, `tools/dayz_mcp/dayz_test_storage.py:678`, `tools/dayz_mcp/dayz_test_storage.py:719`. |
| `storage_backup`, `marker_backup` | Reserved names recorded before mutation | Plain-name and identity checks; joins use recorded names | Retained in C and reporting result; D/K retained | `tools/dayz_mcp/dayz_test_storage.py:434`, `tools/dayz_mcp/dayz_test_storage.py:474`, `tools/dayz_mcp/dayz_test_storage.py:498`, `tools/dayz_mcp/dayz_test_storage.py:844`, `tools/dayz_mcp/dayz_test_storage.py:961`. |
| `launch_allowed`, `storage_recovery_required` | Blocked result sets false/true; successful result true/false | Lifecycle refusal records exact reason and prevents spawn | Not journal fields; derived anew from disk | `tools/dayz_mcp/dayz_test_storage.py:401`, `tools/dayz_mcp/dayz_test_storage.py:958`; `tools/dayz_mcp/process_lifecycle.py:3322`, `tools/dayz_mcp/process_lifecycle.py:4250`. |
| `storage_rotated`, backup, reset notice | Successful moved recovery/rotation returns true; classification returns false | Copied into provisional record; persisted before spawn | Null remains unknown on refusal; observation follows run record. Reseal/crash/retry loses pending true (**M3**) | `tools/dayz_mcp/dayz_test_storage.py:958`, `tools/dayz_mcp/dayz_test_storage.py:1171`; `tools/dayz_mcp/process_lifecycle.py:3322`, `tools/dayz_mcp/process_lifecycle.py:3368`, `tools/dayz_mcp/process_lifecycle.py:4247`. |
| `launch_operation_id` / observation identity | Run identity copied into observation | Loader validates/preserves it; tool compares known identities | Run replacement/pruning updates observation; unknown measurement removes prior entry | `tools/dayz_mcp/process_lifecycle.py:281`, `tools/dayz_mcp/process_lifecycle.py:1819`, `tools/dayz_mcp/process_lifecycle.py:1900`, `tools/dayz_mcp/process_lifecycle.py:1913`, `tools/dayz_mcp/process_lifecycle.py:1961`; `tools/dayz_mcp/dayz_test_tool.py:1966`. |

### 5. State-machine matrix

| Design state / accepted combination | Code continuation | Comparison and evidence |
|---|---|---|
| S0: W=O, no transaction | Classify; reuse or start rotation | Matches ordinary directory state. `tools/dayz_mcp/dayz_test_storage.py:1152`; `_audit_context/SPEC_R9B_IMPL.md:117`. |
| S1: intact P, original M, K absent | `abort` → C(P), then classify | Matches. `tools/dayz_mcp/dayz_test_storage.py:702`, `tools/dayz_mcp/dayz_test_storage.py:921`, `tools/dayz_mcp/dayz_test_storage.py:1151`; `_audit_context/SPEC_R9B_IMPL.md:118`. |
| S2: W absent, D=O, P, original M | `advance_s` | Matches. `tools/dayz_mcp/dayz_test_storage.py:707`, `tools/dayz_mcp/dayz_test_storage.py:872`; `_audit_context/SPEC_R9B_IMPL.md:119`. |
| S3: S, E pending in M | `preserve` M→K | Matches, including E=N. `tools/dayz_mcp/dayz_test_storage.py:719`, `tools/dayz_mcp/dayz_test_storage.py:874`; `_audit_context/SPEC_R9B_IMPL.md:120`. |
| S3a: S, original absent, M/K absent | `publish_n` | Matches. `tools/dayz_mcp/dayz_test_storage.py:711`; `_audit_context/SPEC_R9B_IMPL.md:121`. |
| S4: S, E preserved in K, M absent | `publish_n` | Matches. `tools/dayz_mcp/dayz_test_storage.py:723`; `_audit_context/SPEC_R9B_IMPL.md:122`. |
| S5: S, N published, preservation satisfied | `advance_q`, without moving N into K | Matches. `tools/dayz_mcp/dayz_test_storage.py:716`, `tools/dayz_mcp/dayz_test_storage.py:727`; `_audit_context/SPEC_R9B_IMPL.md:123`. |
| S6: Q, N published, preservation satisfied | `complete` → C(Q) | Matches. `tools/dayz_mcp/dayz_test_storage.py:730`, `tools/dayz_mcp/dayz_test_storage.py:887`; `_audit_context/SPEC_R9B_IMPL.md:124`. |
| S7: C(Q), N, W absent | In completing call, verify N then reseal if needed; fresh call classifies normally | Storage path matches; lifecycle replay has **M3**. `tools/dayz_mcp/dayz_test_storage.py:930`, `tools/dayz_mcp/dayz_test_storage.py:943`, `tools/dayz_mcp/dayz_test_storage.py:1012`; `_audit_context/SPEC_R9B_IMPL.md:125`. |
| S8: C(Q), X, W absent | Verify X; fresh call republishes identical marker as no-op | Storage path matches; C still contains A, exposing **M3**. `tools/dayz_mcp/dayz_test_storage.py:599`, `tools/dayz_mcp/dayz_test_storage.py:950`, `tools/dayz_mcp/dayz_test_storage.py:1169`; `_audit_context/SPEC_R9B_IMPL.md:126`. |
| SA: C(P), intact W | Classify; later rotation reserves a different transaction identity | Matches. `tools/dayz_mcp/dayz_test_storage.py:869`, `tools/dayz_mcp/dayz_test_storage.py:1151`; `_audit_context/SPEC_R9B_IMPL.md:127`. |
| SE: consumer-created W, historical C(Q) | Matching seal reuses; different seal rotates this W | Matches. `tools/dayz_mcp/dayz_test_storage.py:227`, `tools/dayz_mcp/dayz_test_storage.py:233`, `tools/dayz_mcp/dayz_test_storage.py:1161`; `_audit_context/SPEC_R9B_IMPL.md:128`. |
| Any admitted state × partial/full leftover T | Same authoritative continuation | Matches exclusion of temporaries. `tools/dayz_mcp/dayz_test_storage.py:1010`; `_audit_context/SPEC_R9B_IMPL.md:132`. |
| Legacy known original, lagging P/S | Authenticate old seal in M/K; preserve/publish/advance | Corresponding paths exist. `tools/dayz_mcp/dayz_test_storage.py:803`; `_audit_context/SPEC_R9B_IMPL.md:155`, `_audit_context/SPEC_R9B_IMPL.md:180`. |
| Legacy unknown original, P/S | Preserve existing opaque M once; retain existing K; publish/advance | Corresponding conservative paths exist. `tools/dayz_mcp/dayz_test_storage.py:777`; `_audit_context/SPEC_R9B_IMPL.md:156`, `_audit_context/SPEC_R9B_IMPL.md:162`. |
| Legacy Q | Require N; known original additionally requires authenticated K | Corresponding paths exist. `tools/dayz_mcp/dayz_test_storage.py:784`, `tools/dayz_mcp/dayz_test_storage.py:817`; `_audit_context/SPEC_R9B_IMPL.md:158`. |
| Recognized J: invalid world/backup types, both worlds absent/present, J+C, missing D for S/Q | Refuse before applying action | Matches binding refusals, including final-phase W+D. `tools/dayz_mcp/dayz_test_storage.py:647`; `_audit_context/SPEC_R9B_IMPL.md:168`. |
| Recognized schema-2 J: original/preservation/publication mismatch | Refuse | Matches listed physical predicates. `tools/dayz_mcp/dayz_test_storage.py:702`, `tools/dayz_mcp/dayz_test_storage.py:710`, `tools/dayz_mcp/dayz_test_storage.py:730`; `_audit_context/SPEC_R9B_IMPL.md:173`. |
| Physically active J with differently cased prefix | Scanner treats it as no transaction; classification may publish X | **M1:** accepted outside the physical transaction matrix. `tools/dayz_mcp/dayz_test_storage.py:1010`, `tools/dayz_mcp/dayz_test_storage.py:1139`, `tools/dayz_mcp/dayz_test_storage.py:1170`; `_audit_context/SPEC_R9B_IMPL.md:119`. |
| No recognized J, W is file/link/other | Treated as W absent; seal-only may authorize launch | **M2:** accepted outside directory/absence states. `tools/dayz_mcp/dayz_test_storage.py:1154`, `tools/dayz_mcp/dayz_test_storage.py:1172`; `_audit_context/SPEC_R9B_IMPL.md:117`, `_audit_context/SPEC_R9B_IMPL.md:168`. |

No missing continuation was found for the listed canonical schema-2 rows or the specified legacy branches.

## FINDINGS

### M1 — P0 — Differently cased journal prefix bypasses recovery and can overwrite original marker bytes

**Locations:** `tools/dayz_mcp/dayz_test_storage.py:1010`, `tools/dayz_mcp/dayz_test_storage.py:1139`, `tools/dayz_mcp/dayz_test_storage.py:1170`.

The scanner’s first filter is case-sensitive:

```python
if not name.startswith(JOURNAL_PREFIX):
    continue
```

The Win32 identity predicate used for reserved-name collisions is not applied here.

**Executable scenario:** Seed S2: W absent, D containing O, M containing invalid original bytes E, K absent, valid schema-2 J(P) recording E’s hash. Give J the listed name `STORAGE_1.modset.rotation.<txid>.json`. Call `prepare_storage(..., seal=X, ...)`.

The scanner returns no active journal. Classification takes `seal_only`, replaces E with X, and permits launch. J remains active and E was never preserved into K. This is original-marker data loss; the world backup itself remains intact.

The scanner subcase is directly executable with only a mocked directory listing:

```python
with patch.object(s.os, "listdir", return_value=[
    s.JOURNAL_PREFIX.upper() + "1" * 32 + s.JOURNAL_SUFFIX
]):
    assert s._active_journals("M:\\mission") == ([], False)
```

### M2 — P2 — Ordinary classification equates unsupported world types with absence

**Locations:** `tools/dayz_mcp/dayz_test_storage.py:1154`, `tools/dayz_mcp/dayz_test_storage.py:227`, `tools/dayz_mcp/dayz_test_storage.py:1169`, `tools/dayz_mcp/dayz_test_storage.py:1172`.

```python
storage_present = _entry_kind(ntpath.join(mission, STORAGE_NAME)) == "dir"
```

A file, link, or other object becomes `False`, exactly like absence. The subsequent branch publishes a marker and returns `launch_allowed=True`.

**Executable scenario:** Seed a mission directory with no J, a regular file named `storage_1`, and no M. Call `prepare_storage` with valid seal/project/time/txid. Observe a newly published M and an allowed result while W remains a file.

The active-journal selector rejects this type; the ordinary classification entry does not.

### M3 — P2 — Completed-journal replay loses reset evidence after recovery reseals A→X

**Locations:** `tools/dayz_mcp/dayz_test_storage.py:943`; `tools/dayz_mcp/process_lifecycle.py:165`, `tools/dayz_mcp/process_lifecycle.py:3315`, `tools/dayz_mcp/process_lifecycle.py:3372`, `tools/dayz_mcp/process_lifecycle.py:4247`.

Recovery correctly retains `new_seal=A` in C while publishing X into M. The lifecycle replay reader requires:

```python
document.get("new_seal") == seal
```

**Executable scenario:**

1. Seed an admitted moved transaction for A.
2. Recover for X≠A; stop after M becomes X and J becomes C, before the lifecycle measurement is persisted.
3. Retry a fresh launch for X without creating W.
4. `prepare_storage` ignores C and returns seal-only/non-rotation.
5. `_pending_completed_rotation(mission, X)` skips the valid C because it records A.
6. Lifecycle persists `storage_rotated=False`, with no backup/reset notice.

The retained disk state still represents a reset awaiting replacement storage, but the run record and observation log report false. No original-world loss follows from this finding.

### M4 — P3 — Journal naming remains duplicated outside a shared naming helper

**Locations:** `tools/dayz_mcp/dayz_test_storage.py:485`, `tools/dayz_mcp/dayz_test_storage.py:529`, `tools/dayz_mcp/dayz_test_storage.py:849`, `tools/dayz_mcp/dayz_test_storage.py:984`, `tools/dayz_mcp/dayz_test_storage.py:1054`; `tools/dayz_mcp/process_lifecycle.py:131`.

J/C names are independently assembled by validation, completion, observation, reconciliation, and production. Both scanner grammars also embed independent literals.

**Executable scenario:** Run:

```powershell
rg -n 'JOURNAL_PREFIX \+|_JOURNAL_ACTIVE|_COMPLETED_ROTATION_JOURNAL' tools/dayz_mcp/dayz_test_storage.py tools/dayz_mcp/process_lifecycle.py
```

The output enumerates the repeated construction sites and independent grammars. Current canonical spellings agree; this is the explicitly requested outside-helper finding, not a demonstrated current spelling mismatch.

## NOT VERIFIED

- Findings were derived by grep and table walks; the executable scenarios above were not run.
- No fault-injection suite, fixed-point enumeration, or independent review was performed.
- Real NTFS alias resolution, case-preserving enumeration, 8.3 names, reparse points, handle-sharing failures, and power-loss durability remain unverified.
- No DayZ, daemon, MCP tool, real mission folder, or `%LOCALAPPDATA%` was accessed. No files were modified.