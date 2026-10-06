# Triage bf5c+8cf9

Tickets: fb-20260930-180331-bf5c, fb-20260930-214647-8cf9

Title: binarize con -addon=%TEMP% convierte fixtures de test de Temp en parches globales: rompe todo build desde el 24-09 / AB pasa -addon=<padre del source>: todo DayZ Projects expuesto al veneno config.cpp (2do caso)

First pass by GLM-5.3-Flash-EXL3 on the GX10 (API direct, grep excerpts of origin/main 6671fd2, no whole-tree access). Raw model output below, unedited.

<!-- ticket bf5c+8cf9 | model GLM-5.3-Flash-EXL3 | finish stop | 249s | usage {"prompt_tokens": 39449, "completion_tokens": 2724, "total_tokens": 42173, "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 1294}} | grep ['AppData', 'build_failed', 'TICKET', '_backups', 'EndOfFile', 'AddonBuilder', 'ajenos', 'commandline', 'montar', 'parsea', 'proyecto', 'Projects', 'agresivo', 'aunque'] -->

## VERDICT
OPEN_CONFIRMED — the worker still hands `source` and `temp` straight to the AddonBuilder broker with no isolation of the `-addon` tree and no RPT parsing on failure, and CHANGELOG.md explicitly lists the binarize `-addon` poisoning (bf5c, 8cf9) as an open Known issue.

## EVIDENCE
- `tools/dayz_mcp/dayz_test_worker.py:736-747` — the ADDON_BUILDER broker frame passes `"source": source` and `"temp": ntpath.join(runtime.build_temp_root, runtime.mod)` verbatim; there is no dedicated staging parent, junction, or exclude.lst, so whatever AddonBuilder derives as `-addon` (parent of source, or the temp root) is unmitigated.
- `tools/dayz_mcp/dayz_test_worker.py:748-755` — any non-zero exit or bad PBO collapses to a bare `raise _failed("build_failed")`; suggestion (c) of ticket 8cf9 (parse `AddonBuilder.rpt` and expose the real error) is not implemented.
- `tools/dayz_mcp/dayz_test_worker.py:735` — the only related mitigation is `pack_only` when the source has no binarizable assets, which avoids binarize entirely but does not help a project that genuinely has `.p3d`/`.paa`/`.rvmat`.
- `tools/dayz_mcp/dayz_test_worker.py:97-104` — `WorkerRuntimePolicy` shows `dev_root`, `mods_root`, etc., but `build_temp_root` is not among the shown fields; its value/derivation is NOT SHOWN, so I cannot confirm whether it already points to a controlled, empty directory.
- `CHANGELOG.md:154` — Known issues: "binarize reads every `config.cpp` under the folder AddonBuilder passes as `-addon`, which can be the parent of the source or a temp root, so a broken `config.cpp` elsewhere fails the build (bf5c, 8cf9)" — acknowledged, unfixed.
- `CHANGELOG.md:137` — a prior partial fix: `pack-addon.ps1` passes `-packonly` "so AddonBuilder does not binarize against every `config.cpp` under `P:\`", confirming the mechanism is known and only the pack-only path is covered.
- `tools/dayz_mcp/doctor.py:1127` — the doctor already skips `_backups` siblings in launcher scans, showing the codebase is aware of poisoned sibling trees, but no equivalent preflight exists for the build path.

## ROOT CAUSE
AddonBuilder invokes `binarize.exe` with `-addon=<parent of source>` (or the temp root); binarize parses every `config.cpp` under that tree as global config patches, so a broken foreign `config.cpp` (test fixtures, sibling projects like `LFSecure_dev`) fails the whole build with exit 1, which the worker surfaces only as the opaque `build_failed`.

## PROPOSED FIX
**Option 1 (recommended, closes both tickets):** in the build path (`dayz_test_worker.py` build branch, or the ADDON_BUILDER handler in `native_launcher_backend.py` — NOT SHOWN, the exact composition site), stage the source under a dedicated empty parent before invoking the broker: create `<build_temp_root>/<mod>/stage/` containing only a junction (or copy) named after the project basename pointing at the real source, and pass that staged path as `"source"`. Before: `-addon` resolves to `DayZ Projects` (or `%TEMP%`) and any foreign `config.cpp` poisons the build. After: `-addon` resolves to a directory whose only child is the project. Keep the existing contract: success → `exit_code 0` + `pbo_size > 0`; failure → `build_failed`, but additionally parse `DayZ Tools\Bin\Logs\AddonBuilder.rpt` and attach the first `Error N` line as a bounded detail field so the caller sees why.

**Option 2 (smallest, diagnostic only):** keep the invocation as-is and only parse `AddonBuilder.rpt` on the `build_failed` path (dayz_test_worker.py:755), exposing the binarize error text. This does not stop the poisoning but makes it diagnosable; pair with a preflight warning when foreign `config.cpp` files exist under the derived `-addon` parent.

## TEST
Unit test (unittest): build a fake `WorkerRuntimePolicy` with `build_temp_root` in a temp dir; create the project source and a sibling directory containing a broken `config.cpp`; run the staging/build-frame composition; assert the `source` path sent in the ADDON_BUILDER frame resolves to a parent that contains no `config.cpp` other than the project's own. Before the fix the composed frame's parent contains the poisoned sibling (and the real build would exit 1); after the fix the frame is isolated. Complement with the in-game check from the ticket: place a broken `config.cpp` next to the source and run `dayz_test_run(build=true)` — exit 1 before, PBO produced after.

## SIZE / RISK
M — the staging change touches the build invocation path and must respect the repo's strict reparse-point policy (junctions are rejected elsewhere, e.g. `knowledge.py:473-476`, `pinned_keyfile.py:265-272`), so a junction may need an allowlist or a real copy; main risks are compatibility with the native broker/bridge version (the frame shape at dayz_test_worker.py:736-747 is sealed) and binarize behaving differently through a junction or copied tree.

## NOT VERIFIED
- `native_launcher_backend.py` / `native_broker_protocol.py`: the actual AddonBuilder command-line composition, including where `-addon` and `-noLogs` are set — NOT SHOWN; the fix's exact insertion point lives there.
- `WorkerRuntimePolicy.build_temp_root` value and derivation (dayz_test_worker.py, beyond line 104) — NOT SHOWN; it may already be a controlled dir, which would narrow ticket bf-20260930-180331 to the temp-tree poisoning only.
- Whether the 11-directory quarantine from ticket bf-20260930-180331 is still in place on the host.
- Whether AddonBuilder honours `exclude.lst` from the worker, or accepts a redirected `-addon`, on DayZ Tools 1.0.240639.
- The Dokan `P:` drive interaction noted in CHANGELOG.md:154 (a97e), which could interact with any staging/junction fix.