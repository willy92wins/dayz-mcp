# gpt-6.1-sol review, g5, round 4 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

### F8 — P2 — Selector validation still permits inconsistent or silently ignored selections

Anchors: [server_cli.py:223](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/server_cli.py:223), [install_mcp.py:835](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/install_mcp.py:835), [p0s_gate.py:310](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/p0s_gate.py:310), [secure_launcher.py:291](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/secure_launcher.py:291), [install-mcp.ps1:61](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/install-mcp.ps1:61), [install-mcp.ps1:66](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/install-mcp.ps1:66).

**Executable scenarios:** Python entry points were executed with downstream effects mocked. PowerShell ran this tree’s script with `-NoProfile -ExecutionPolicy Bypass -File … -ValidateOnly`. Selector environment variables were absent.

| Input | Observed wrong result |
|---|---|
| `install_mcp.parse_args(["--inst", "UPPER", "--port", "8775"])` | Accepted; `instance_token=None`. |
| `p0s_gate.main(["backup-runs-v1", "--inst", "UPPER", "--port", "8775"])` | Returns `0`, reports `verified`, invokes backup against the **default** paths. |
| `secure_launcher.main(["launcher", "--inst", "UPPER"])` | Invokes the launcher without `instance_token`. |
| Installer `--instance 130 --inst other --port 8775` | Accepted; selects `130` instead of rejecting duplicate selectors. |
| PowerShell `-Instance 130 -Port 8775 -GamePath C:relative` | Returns `0`; Python’s validator rejects the drive-relative path. |
| PowerShell `-Instance 130 -Port 8775 -GamePath \relative` | Returns `0`; Python’s validator rejects the path. |
| PowerShell parameter value `"130\n"` containing an actual trailing LF | Returns `0`; the server rejects it as `invalid_instance_token`. |

Argparse accepts abbreviations that the shared selector scanner does not consume. PowerShell separately uses rooted-path detection rather than absolute-path validation, and its `$` regex anchor admits a trailing newline.

**Consequence:** invalid selector requests can proceed into installation, default-root backup, or default-policy launch. Invalid PowerShell selections pass the pre-effect guard and fail only downstream.

**Fix:** disable abbreviation in the affected Python parsers, including the backup subparser, and validate the selector consumed by the parser. Make PowerShell’s token and absolute-path checks equivalent to the server’s checks. Add the scenarios above as regressions.

### F1 — P3 — Unknown interpreter flags still bypass conservative classification

Anchor: [identity_migration.py:719](C:/Users/guill/dzmcp_gauntlet/g5_instances/ws/tools/dayz_mcp/identity_migration.py:719).

Supplying process metadata for:

```text
C:\fixture\legacy\.venv\Scripts\python.exe -z -m dayz_mcp --daemon --keyfile C:\keys\legacy.key
```

produces `_interpreter_target=(None, None, False)`, but disposition is `absent` and scanning returns `()`. Writer recognition exits before the new conservative accreditation check.

This misses the directive’s unknown-flag rule. **P3:** the installed CPython rejects `-z`; I have not established a live writer using this input. Preserve recognizable DayZ target evidence when import-mode parsing is unestablished.

## SPEC COVERAGE

Coverage is limited to the binding round 4 closure directive.

| Requirement | Status |
|---|---|
| F1: `-I`/`-P` prevent cwd accreditation and retain migration blocker | **done**; original scenario and additional compact-flag variants verified. |
| F1: unknown interpreter flags retain blocker | **wrong**; recognition returns `absent` first. |
| F1: document foreign `PYTHONSAFEPATH` limitation without reading foreign environments | **done**. |
| F7: named Python installation passes explicit ownership and `sync=False`; main succeeds | **done**. |
| F8: reject the previous table’s exact inputs and inherited conflicts | **done**. |
| F8: consistent selector grammar, duplicate rejection and absolute game paths across entry points | **wrong**; F8 above. |
| PowerShell `-ValidateOnly` exits before installation effects | **done**, verified on this tree. |
| F10: always replace shared root; restore on close and constructor failure | **done**, verified with filesystem dependencies mocked. |
| Preserve F2–F6 and reverted launcher source; leave F9 withdrawn | **done** in the round 3-to-4 comparison. |
| Add regressions and `ROUND 4 FIXES` report section | **done**, but regressions miss the remaining selector cases. |

## GATE GAP

The tests cover exact selector spellings, ordinary relative paths, and ordinary malformed tokens. They miss argparse abbreviations, drive-relative paths and trailing-newline tokens. Thus the supplied passing gate does not establish consistent selector validation.

## PREMISE

The closure scope is valid. F9 remains withdrawn; deferred launcher resealing and live acceptance are not reopened here.

## NOT VERIFIED

- Full suite independently rerun: relied on the orchestrator’s supplied measurements.
- Fourteen round 4 tests passed with mocked filesystem dependencies and process-scoped PowerShell execution-policy override. Unmodified PowerShell tests initially stopped at execution policy.
- Live registration, previous-tree installers, sealed deployment and simultaneous DayZ launch were not executed.

No files were modified; no pip, registration changes, user-data writes or game launches were performed.