VERDICT: CHANGES_REQUIRED

## FINDINGS

### F1 — P2: Missing project identity bypasses recorded-anchor validation

**Locations:** `tools/dayz_mcp/launch_logs.py:144–145`, `tools/dayz_mcp/server.py:2408`, `tools/dayz_mcp/server.py:5402`.

`_recorded_anchor_in_sealed_project` returns `True` when `mod` is absent, empty, or not a string. This preserves a fail-open route through the round 2 project check.

**Executable scenario:**

- Bind instance `130`; approved project root is `C:\approved`.
- Lifecycle supplies:
  ```python
  {
      "run_id": "R",
      "state": "RUNNING",
      "profiles": r"C:\unapproved\_server\profiles-130",
      "processes": [{
          "role": "server",
          "pid": 1,
          "creation_time_utc": "2026-10-07T00:00:00Z"
      }]
  }
  ```
- The foreign directory contains a fresh `script.log` with `FOREIGN_READY\n`.
- Call `logs_since(run_id="R")` and `wait_for(condition="log_matches", pattern="FOREIGN_READY", lookback_from="launch")`.

**Reproduced wrong outputs:**

- `logs_since` returns the foreign path and `FOREIGN_READY`.
- `log_matches` returns `satisfied: true`.
- The sealed-project loader is called **zero times**, although `_validated_recorded_leaf(approved_policy, run)` returns `None`.

These probes exercised the real readers with filesystem I/O mocked; no files or processes were created.

**Fix:** reject rows without a resolvable sealed project. Update positive log fixtures to carry valid project identity instead of retaining a production bypass for incomplete fixtures.

### F2 — P2: Existing regression fixtures were not adapted to the strengthened extension contract

**Locations:** `tools/dayz_mcp/dayz_test_tool.py:604`, `tools/tests/test_db05_preflight_diagnostics.py:20`, `tools/tests/test_steam_not_running_preflight.py:146`, `tools/tests/test_dayz_test_tool_modes.py:464`, `tools/tests/test_client_lifecycle_7055_9336_9efc.py:192`.

The new extension check requires a valid recorded `profiles` anchor. Existing positive fixtures omit it; the client-admission fixture also supplies a policy without `dev_root` at `test_client_lifecycle_7055_9336_9efc.py:53`.

**Executable scenario:**

From `tools/`, run:

```text
python -B -m unittest tests.test_db05_preflight_diagnostics.Db05DiagnosticsTest.test_real_success_keeps_existing_envelope tests.test_steam_not_running_preflight.SteamNotRunningToolGateTests.test_negative_steam_running_launch_proceeds
```

**Reproduced wrong test output:** both raise `DayzTestToolError: lifecycle_status_invalid` before their intended launch assertions.

Adding only `P:\ExampleMod_Suite\_server\profiles` to the fixtures **in memory** makes five reported failing Steam/diagnostic tests pass, including success, replacement refusal, repeated rejection, and remediation opt-in.

This demonstrates incomplete regression maintenance, **not a demonstrated launch regression for valid recorded runs**. Separate adapter probes successfully launched valid default and named client extensions.

**Fix:** supply complete approved policies and valid recorded anchors in the affected existing fixtures, preserving their original assertions. Keep invalid-anchor rejection intact and rerun the orchestrator’s fast gate.

### F3 — P2: Required launcher reseal and bundle verification are missing

**Locations:** `REPORT.md:44`, `REPORT.md:53`, `tools/packaged-modules.lock.json:7`, `:12`, `:20`.

The report explicitly defers rebuilding. Updating source hashes does not deliver the required sealed launcher.

**Executable scenario:**

From `tools/`:

```text
python -B -c "from pathlib import Path; import build_native_launcher as b; b.verify_bundle(Path('native-launchers/dayz-test-v1'))"
```

**Reproduced output:** `FileNotFoundError` for `closure-manifest.json`. This directory also lacks `app.pyz` and `build-contract.json`.

The three source-lock checks pass, and builder/verifier module sets match. Neither proves a rebuilt launcher contains these changes.

**Fix:** complete the authorized packaging handoff and provide successful resealed-bundle/import-closure verification before declaring batch acceptance.

## SPEC COVERAGE

“Done” below means implemented or test coverage present by inspection; it does **not** imply that sandbox-blocked tests passed.

| Specification requirement | Status |
|---|---|
| Provision during admitted preparation, before config seeding; no installer prerequisite | **Done** |
| Scope excludes bridge, persistence-format and shutdown redesign | **Done** |
| Pure explicit-token helper; exact default/named leaf; validator reuse | **Done** |
| Worker and attestation use canonical payload token | **Done** |
| VPP uses canonical request token | **Done** |
| Preparation and daemon readers use bound selection | **Done**, except project admission in F1 |
| Preserve absolute-path and owner-name rejection | **Done** |
| Existing project and appropriate role parents required | **Done** |
| Expected leaf and seeding credentials checked before absent-leaf creation | **Done** |
| Create only leaf; reuse ordinary directories; reject files and escaping redirections | **Done** |
| Recheck concurrent creation | **Done** |
| Preserve atomic seeding, endpoint/key checks, UUID write and reread | **Done** |
| Typed preparation failures; no spawn after preparation failure | **Done** |
| No migration, copying, foreign-config overwrite or cleanup deletion | **Done** |
| Planned artifacts derive from project/mode and selected token | **Done** |
| Existing-run artifact, diagnosis, stop and close paths validate recorded anchors | **Done** |
| Reject relative, foreign-project, foreign-token, legacy and traversal anchors | **Done** in tool resolver; **Wrong** in log resolver for unidentified projects |
| Retain mode authority and server-anchor stop expansion | **Done** |
| Log admission and siblings retain the selected leaf | **Done** |
| Logs and capture-window resolution validate the sealed project first | **Wrong — F1** |
| Preserve cursors, scan limits, launch filtering and deadlines | **Done** by change inspection |
| Preserve aggregate `log_matches` and role-selected `file_matches` | **Done** |
| Preserve client/offline eligibility | **Done** |
| Close watches and logout reads use named folders | **Done** |
| Preserve freshness, rotation, retirement and shutdown ordering | **Done** by change inspection |
| Attestation boundaries and initialization reads receive the same token | **Done** |
| Preserve empty/unreadable boundaries and complete-line requirements | **Done** |
| VPP remains read-only; `serverDZ.cfg` unchanged | **Done** |
| Client diagnosis receives named roots; baseline/ceiling algorithms retained | **Done** |
| Default paths/order/config serialization and missing-folder behavior retained | **Done** by inspection; compatibility gate remains **Wrong — F2** |
| Reviewed source hashes and builder/verifier module parity | **Done**, independently checked |
| Launcher rebuild, reseal and packaged import closure | **Missing — F3** |
| PBO unchanged | **Done** |
| Required README and tool documentation | **Done** in the live tree |
| Manifest/intent formats and recovery behavior retained | **Done** |

Regression acceptance:

| Required group | Status |
|---|---|
| Fresh named preparation and fail-closed preparation | **Done:** fixtures present; execution blocked here |
| Mode/path parity and default compatibility | **Missing:** full launch-consumer/public-boundary acceptance; existing gate remains red |
| Log admission/siblings | **Wrong:** F1 bypass remains |
| `logs_since` / `log_matches` | **Wrong:** foreign unidentified-project evidence satisfies |
| `file_matches`, including invalid-anchor no-open | **Done:** tests present; execution blocked here |
| Close/logout and graceful metrics | **Done:** tests present; execution blocked here |
| RPT rotation and old-line rejection | **Done:** tests present; execution blocked here |
| Stop artifacts after client retirement | **Done:** assertions present |
| Client diagnosis and dump isolation | **Done:** tests present; execution blocked here |
| Attestation sources, boundaries and roles | **Done:** tests present; execution blocked here |
| VPP named-state isolation | **Done:** tests present; execution blocked here |
| Recovery and foreign labeling | **Done:** tests present |
| Packaging | **Missing:** resealed-bundle acceptance |

**Previous round:** F1 is only partially fixed; F2’s traversal rejection is fixed; F3’s extension rejection is fixed. The latter two focused tests pass. A foreign named extension also fails before the mocked launch consumer, while valid default and named extensions succeed with the expected artifacts.

## GATE GAP

The positive log-reader fixtures omit `mod` (`tools/tests/test_g5fix_named_profiles.py:364`), so they exercise the bypass rather than successful sealed-project validation. The new foreign-project negative includes `mod`; together these tests miss F1.

The artifact test constructs `_compact_result` directly (`tools/tests/test_g5fix_named_profiles.py:307`), which cannot prove that the public adapter supplies correct artifacts. Source-lock checks similarly cannot detect an absent or stale packaged launcher.

## PREMISE

The batch’s functional premise remains sound; no PBO change is needed.

The reported build prohibition conflicts with the binding reseal requirement. Assigning packaging to the orchestrator can resolve ownership, but deferral alone does not satisfy acceptance.

The supplied patch also omits `README.md` and `PROJECT-MAP.md` changes present against the supplied base. I inspected the live files.

## NOT VERIFIED

- No DayZ, daemon or MCP lifecycle processes were started.
- Full filesystem tests could not complete: the read-only sandbox prevents temporary-directory creation. Those failures are not attributed to the implementation.
- The complete orchestrator gate was not reproduced here; two reported errors were independently reproduced without filesystem writes.
- No rebuilt launcher, packaged import closure or in-game role/logout/termination evidence was verified.
- No files, commits or persistent review artifacts were written.

