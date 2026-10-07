# gpt-6.1-sol review, g5, round 3 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Finding IDs retain the previous round’s numbering. Four blockers remain.

### F1 — P1 — Migration accreditation still ignores Python’s import mode

Anchors: `tools/dayz_mcp/identity_migration.py:726`, `tools/dayz_mcp/instance_context.py:85`.

The resolver always considers cwd:

```python
candidates.append(Path(cwd) / "dayz_mcp")
```

It receives no interpreter flags, so it cannot distinguish normal module lookup from `-I`, which excludes cwd.

**Executable scenario:** an isolated interpreter has the legacy, hardcoded `DayZ_MCP_130` package installed in its environment, with cwd set to this workspace’s `tools` directory:

```text
C:\fixture\legacy\.venv\Scripts\python.exe -I -m dayz_mcp --daemon --keyfile C:\keys\legacy.key
```

While migrating `130`, executing the real classifier and scanner with that process metadata and this workspace’s actual contract marker produced:

```text
disposition = not_writer
scan = ()
```

The scanner accredits the cwd package, although the interpreter executes its installed package. The unknown legacy writer should block migration. I separately verified that isolated Python excludes cwd from `sys.path`; I did not launch the legacy daemon.

**Fix:** incorporate interpreter import flags and positively establish import resolution. If resolution cannot be established, retain the blocker. The original keyfile-value reproduction is fixed, but F1’s authority requirement remains incomplete.

### F7 — P2 — Python’s named registration succeeds, then its knowledge installation fails

Anchors: `tools/install_mcp.py:1377`, `tools/dayz_mcp/knowledge_pack.py:285`.

The Python entry point still invokes:

```python
sync=options.register
```

The knowledge installer correctly refuses synchronization by a named owner.

**Executable input:**

```text
python -B install_mcp.py --instance 130 --port 8775 --register
```

I executed the real `main`, with successful registration and pack filesystem preparation mocked, and the named context bound as `run_installer` binds it.

**Observed:**

```text
{"error": "global_skills_not_owned", "status": "error"}
main rc = 2
```

This occurs after `run_installer` has committed registration. The ordinary named installation therefore reports failure after changing host configuration.

**Fix:** named installations should install their pack without requesting global synchronization. Pass explicit ownership and select `sync=False` for named instances, as the PowerShell branch now does.

### F8 — P2 — Selector rejection remains inconsistent across entry points

Anchors: `tools/install-mcp.ps1:45`, `tools/p0s_gate.py:302`, `tools/dayz_mcp/secure_launcher.py:278`.

The PowerShell guard uses case-insensitive `-notmatch`; the other two entry points directly accept argparse’s glued and duplicate forms.

**Executable scenarios and observed results:**

| Entry point/input | Wrong result |
|---|---|
| PowerShell `-Instance UPPER -Port 8775` | Accepts `UPPER`; the server validator rejects it. |
| PowerShell `-Instance=130 -Port 8775` | Leaves `Instance=""`; treats the request as default. |
| PowerShell `--instance=130 -Port 8775` | Also leaves `Instance=""`. |
| `p0s_gate.py backup-runs-v1 --instance 130 --instance other --port 8775` | Returns verified and selects `DayZ_MCP_other`. |
| Secure launcher `launcher --instance 130 --instance other` | Calls the launcher with `instance_token="other"`. |
| Either Python parser with `--instance=130` | Accepts the prohibited glued form. |

PowerShell observations came from executing its actual parameter block and validation prefix in memory. The gate and launcher observations used their real entry points with downstream writes/launches mocked.

The PowerShell prefix also accepts `-GamePath relative` and conflicting inherited instance context. It contains no environment-selector validation before installation writes begin at `tools/install-mcp.ps1:657`.

**Fix:** apply the shared rejection rules before effects at every required entry point. Reject unconsumed PowerShell selector arguments, use case-sensitive token validation, and validate game path and inherited context before installation.

### F10 — P2 — The shared lifecycle fixture still escapes its temporary root

Anchors: `tools/tests/lifecycle_helpers.py:158`, `tools/tests/lifecycle_helpers.py:227`.

The fixture only supplies a root when none exists:

```python
if not os.environ.get("DAYZ_MCP_SHARED_ROOT"):
```

Its `close()` removes the temporary directory without restoring the environment.

**Executable scenarios:** using the real fixture constructor/cleanup with temporary-directory and filesystem dependencies mocked:

```text
A root=C:\fixture\A  shared=C:\fixture\A\shared-root
A.close()
B root=C:\fixture\B  shared=C:\fixture\A\shared-root
inside B=False
```

With an inherited override:

```text
DAYZ_MCP_SHARED_ROOT=C:\Users\guill\Documents\dayz-locks
fixture root=C:\fixture\C
shared=C:\Users\guill\Documents\dayz-locks
inside fixture=False
```

The real admission path resolver then requests creation of that user directory and returns:

```text
C:\Users\guill\Documents\dayz-locks\box-admission.lock
```

Thus tests using this fixture can recreate an expired fixture directory or write under the user’s home. The new `ProcessLifecycleTest` isolation does not cover this helper.

**Fix:** always inject a fixture-owned root, save and restore the previous environment value, and restore it during exception cleanup too.

## SPEC COVERAGE

“Done” below means source coverage established in this bounded review; it does not imply live acceptance.

| Specification bullet | Status |
|---|---|
| **1:** Optional configuration/installer/CLI selector fields | **done** |
| Token grammar; duplicate, malformed and glued rejection everywhere | **wrong — F8** |
| Default argv, registration payload, paths, keyfile and receipt contracts | **done** at source level; sealed-executable exception withdrawn |
| One bound context; environment conflicts rejected before effects | **wrong** in PowerShell |
| Omit registration `env`; retain bound process context | **done** |
| **2:** Propagation through listed server, policy, doctor and stdio surfaces | **done** |
| Append named flags after existing daemon argv, including optional flags | **done** |
| WMI command derives from provenance and carries selector/game path | **done** by source trace |
| Registration-name binding and both-host field comparison | **done** |
| Selected registration retained in policy revalidation | **done** |
| Accreditation before credential/HTTP transmission | **done** in retained paths |
| **3:** Lifetime root exclusion and independent same-process ownership refusal | **done**; in-memory second activation refused before migration/coordination |
| Preserve paths/formats; no default-store migration | **done** |
| **4:** Valid-client exclusion | **done** |
| Actual Python target/import resolution before accreditation | **wrong — F1** |
| Unknown writers block; different-root exemptions require accreditation | **wrong — F1** |
| Native identity, PID-reuse, ancestor and `allowed_seen` handling | **done** in retained paths |
| Selector on recovery and post-copy quiescence checks | **done** |
| Settled-receipt shortcut retained | **done** |
| Explicit `p0s_gate` selection and pre-write validation | Selection **done**; grammar **wrong — F8** |
| Existing `130` receipt validates unchanged at its actual paths | **missing** live evidence |
| **5:** Runtime, inbox, knowledge, frame, capture and audit isolation; default fallbacks | **done** |
| Shared host-config recovery authority | **done** |
| Named installer probes, parsing, timeout targets and rollback routing | **done** in Python source |
| Both installers’ dynamic not-found handling | **done** by source inspection; live CLI behavior unverified |
| Named bridge templates and profiles; endpoint/key validation | **done** |
| Explicit global-skills ownership with usable named installs | Policy **done**; Python entry point **wrong — F7** |
| **6:** Shared box admission around checks, preparation and spawn | **done** in retained implementation; simultaneous acceptance pending |
| OS release on daemon death and retained recovery protections | **done** by source mechanism; live behavior unverified |
| Independent leases and stopping authority | **done** |
| Independent target/temp build locks with deterministic acquisition/release | **done**; both overlapping-resource constructions share a lock |
| **7:** Named Experimental reseal and approved-launcher evidence | **missing**, deferred deployment |
| Packaged closure inventories updated | **done** |
| Unchanged default executable remains accepted | **Withdrawn by binding directive**; coordinated default reseal required |
| No PBO rebuild; deployed endpoint compatibility | No rebuild **done**; deployed compatibility **missing** acceptance |
| **Round-3 isolation directive:** every affected fixture stays temporary | **wrong — F10** |

Previous findings F2, F3, the Python subprocess propagation in F4, request-based worker root in F5, and overlapping-resource locking in F6 are fixed within the checks performed. F9 is superseded by the orchestrator’s reseal decision.

## GATE GAP

The reported gate can miss these defects because:

- The F1 regression covers a keyfile mistaken for a target, but not isolated import resolution.
- PowerShell tests inspect source strings rather than exercising parameter binding.
- Named skill tests exercise ownership refusal, not the complete Python installer success path.
- Fixture isolation tests cover one fixture class and directory existence, not inherited overrides, environment restoration, or writes inside existing directories.
- Lock-path intersections and mocked worker-root lookup do not prove real concurrent packaged builds or simultaneous launch admission.

## PREMISE

The unchanged-default-bundle requirement is explicitly withdrawn; stale-pin rejection is therefore not a blocker.

The suite-structure offenders and two fast-tier failures are reported as baseline failures, so I have not treated them as new defects.

`REPORT.md` contains stale earlier-round statements about installer scope and C++ selector propagation. The supplied diff contains no `launcher.cpp` change; its removal matches the round-3 directive.

## NOT VERIFIED

- Full writable test execution: my 31-test attempt ended with **16 errors because the sandbox cannot create temporary directories**. Six write-free regressions passed.
- Real OS lock contention and release, filesystem migration fault boundaries, or live registration rollback.
- Both upgraded daemon installations, unchanged `130` receipt acceptance, rebuilt/resealed launchers, and deployed bridge routing.
- Simultaneous-launch negative acceptance and final status from both instances.

No files were modified and no DayZ processes were managed.