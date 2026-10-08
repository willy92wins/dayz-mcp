# Review brief: named-instance profile folders (follow-up to g5, #216)

## Task
g5 (independent instances, #216) is merged; the current directory is `origin/main` `bbda2e9` with it. The in-game
acceptance (2026-10-07, DayZ 1.30 Experimental, instance `130`, project `DayZ_MCP`) found a defect in how a named
instance's per-role profile folders are provisioned and read. The owner decided: merge g5, fix this in a follow-up
batch. Verify the finding against the code and write the binding implementation specification for that batch.

## The finding (orchestrator's observation; verify, do not trust)
1. A named instance launches each role with `-profiles=<dev_root>\_<role>\profiles-<token>`
   (`tools/dayz_mcp/dayz_test_worker.py:452-460`), and the instance binding refuses any other folder name
   (`tools/dayz_mcp/loopback.py:1817-1819`, `instance_profile_owner_mismatch`).
2. The bridge config seeding writes `dayz_mcp.json` only into a folder that already exists
   (`tools/dayz_mcp/loopback.py:1789`; docstring at `:1776-1784`: a mistyped dev_root must still fail closed).
   Nothing creates `profiles-<token>`. The first `130` launch answered `instance_config_missing`
   (`_g5fix_evidence/g5.20-130-launch.txt`), through `prepare` or the generic `except Exception` at
   `tools/dayz_mcp/process_lifecycle.py:2262-2263`. After the operator created empty
   `C:\temp\DayZ_MCP_130_dev\_server\profiles-130` and `_client\profiles-130`, the same call succeeded
   (`g5.21-130-launch-retry.txt`), and `runs.json` recorded the `profiles-130` folder
   (`runs-130-after-close.json`).
3. `dayz_test_run`'s `artifacts_paths` still names `_server\profiles` and `_client\profiles` for that run
   (`g5.21`). `dayz_test_close` reported `graceful=false`, `exit_metrics_valid=false`, `reason=role_without_rpt`,
   `termination_line=false` for both roles (`g5.40-130-close.txt`), although the RPTs in the `profiles-130`
   folders end with `--- Termination successfully completed ---` (`profile_dirs.txt`).
4. Places that build or recognise the legacy name on their own (a grep for `"profiles"`, not classified):
   `dayz_test_attestation.py:313`, `dayz_test_tool.py:800`, `:816`, `:3307`, `launch_logs.py:29`, `:31`,
   `log_tail.py:204`, `native_launcher_transaction.py:158`, `process_lifecycle.py:342`. Readers that take
   `run["profiles"]`: `dayz_test_tool.py:1626`, `:3281`, `launch_logs.py:137`, `server.py:2746`, `:5404`,
   `process_lifecycle.py:4423-4444`, `:4500-4542`, `:7881`.

## What to answer
1. First line, exactly one of: `VERDICT: CONFIRMED` or `VERDICT: NOT_CONFIRMED`.
2. `## FINDINGS`: each defect with severity P1/P2/P3, `path:line`, and an executable failure scenario. Classify
   every place in point 4: which ones must follow the instance's folder (and how: the run's recorded `profiles`,
   or the instance-aware name), which are correct as they are, and why. Include the per-role readers the
   tools use for a named instance's run: `logs_since`, `wait_for` (`log_matches`, `file_matches` with each
   `role`), the close's RPT and logout reads, RPT rotation, client death diagnosis, and
   foreign-process/profile labelling.
3. `## IMPLEMENTATION SPEC`: the spec an implementer follows (files, functions, behaviour, tests that fail before
   and pass after), in the style of your earlier specs. Decide where `profiles-<token>` is created: at launch
   preparation (keeping a mistyped dev_root fail-closed), by the installer, or by the operator with a named
   error. Say whether the default instance (no token, `profiles`) stays byte-identical, and whether anything
   needs a launcher reseal (sealed modules: `tools/packaged-modules.lock.json`), a PBO or a README change.
   Mark the data-critical parts.
4. `## OWNER QUESTIONS` (max 2), only if a choice is the owner's.
5. `## NOT VERIFIED`.
Read-only. Do not start DayZ, daemons or MCP tools, and do not touch `%LOCALAPPDATA%\DayZ_MCP*`,
`C:\temp\DayZ_MCP_130*` or ports 8765/8775. Answer in English.
