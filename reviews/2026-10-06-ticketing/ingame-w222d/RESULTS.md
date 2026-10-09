# Reseal windows for 222d, 2026-10-09: design B, then the namespace/folder refusal

Owner go-ahead for both windows. The orchestrator closes the other sessions' MCP clients only when no game runs, and
never touches a game. The acceptance follows `SPEC_222D_B` §4, and then `SPEC_222D_M` §4.

Paths below without a repository prefix are outside the repository:
- step logs and backups: `C:\Users\guill\DayZ_MCP_backups\w222d-20261009\` and `…\w222d2-20261009\` (`steps.log` in each);
- procedure and scripts: `C:\Users\guill\dzmcp_gauntlet\ingame\` (`DEPLOY_W222D.md`, `DEPLOY_W222D2.md`, `acc_w222d.py`,
  `accept_222d.py`, `argv_sampler.ps1`).

## Comparator
`accept_222d.py check` derives the expected entries of a binarize build from a frozen source inventory and the
sealed include list:
- the include-list files must be byte-identical direct copies;
- `config.cpp` must become a rapified `config.bin`, and each `.p3d` an ODOL model;
- `model.cfg` and `$PBOPREFIX$` are consumed, and the header prefix must equal the namespace.

The deployed PBO is the reference: each reference entry missing from the build is classified.

Calibration: these give FAIL:
- the two builds of the v11b cycle (SimpleGroup by omission, LFHeli_OH1 by prefix);
- in each window, a mutant of the new SimpleGroup build with one entry dropped, and one with the prefix replaced.

The deployed SimpleGroup PBO was built from an older source: 3 scripts are missing from it and 7 differ. The
decisive check is therefore against the frozen source, and the reference only classifies differences.

## Window 1 (20:36-20:52): design B, PR #230 head `05637e3`
- **Quiescence:** the last DayZ server had ended at 20:16. 8 client chains were closed (two Claude sessions, the Codex
  app and two Hermes processes) and both daemons stopped.
- **Reseal:** both trees were resealed. The bundle's `addonbuilder-include.lst` is 104 bytes, equal to the sealed
  literal, and the closure lists it as a `bundle` entry. The 16 regression modules of the spec ran 297 tests, OK. The 2
  skips are symlink-privilege stage tests.
- **SimpleGroup: PASS.**
  - 70 entries with prefix `SimpleGroup`.
  - All 62 include-list files are byte-identical to the frozen source.
  - `config.bin` is rapified, the 6 models are ODOL, and `texHeaders.bin` is the only extra.
  - The sampled AddonBuilder command line is `"<stage>\SimpleGroup" "P:\Mods\@SimpleGroup\Addons" "-prefix=SimpleGroup"
    "-temp=P:\temp\SimpleGroup" "-include=<bundle>\addonbuilder-include.lst" -clear`.
  - The server compiled every script module and logged `[SimpleGroup] Config loaded`. The stack trace about
    `PluginConfigDebugProfile` in the same log also appears in older runs without this change.
- **LFHeli_OH1: FAIL.** `LFHeli_OH1.pbo` has prefix `LFHeli`, the 32 include-list files and `config.bin`, but all 7
  models are missing.
  - binarize was started with the output folder `P:\temp\LFHeli_OH1\LFHeli`, the temp folder plus the prefix.
  - FileBank packed `P:\temp\LFHeli_OH1\lfheli_oh1`, the temp folder plus the lowercase source folder name.
  - The 7 ODOL models were found under `…\lfheli\` (`lfheli-temp-listing.json`).
  - SimpleGroup passed because its namespace equals its folder name.
- **Rollback:** both trees were resealed back on `58132b6`. The bundles are byte-identical to the pre-window
  backups, and the registry differs only in the bundle directory's `file_id`. The deployed PBOs were restored by
  hash (`2C3EAC14`, `194FD1A5`). Inbox `fb-20261009-185248-b855`.

## Owner decision and the second round
"Rechazar desajuste": a binarize build whose namespace differs from the source folder is refused before AddonBuilder
starts, with a specific error and a remediation.
- **Spec:** `SPEC_222D_M` (gpt-6.1-sol).
- **Implementation:** Grok 4.7, one round. gpt-6.1-sol: APPROVED, with one P3 on parity coverage.
- **Receiver checks:** the parity corpus was extended. A case-sensitive worker passed the original corpus and fails
  the extended one. All 11 isolated mutations of the spec now fail the module. gpt-6.1-sol approved that test
  change. The whole suite gave 1 failure, the known environment-only daemon-venv test.
- **PR #230:** head `1380e0c`, CI 4/4.

## Window 2 (22:19-23:49): PR #230 head `1380e0c`
- **Quiescence and reseal:** only this session's two client chains were open, and no daemon ran. Both trees were
  resealed, with the include list equal to the literal. The 19 regression modules ran 428 tests, OK.
- **Interruption.** The harnesses could not start: `daemon_provenance_conflict` at `tools/dayz_mcp/host_config.py:255`.
  - At 20:55 `~/.codex/config.toml` had been rewritten with `enabled = true` added to both `[mcp_servers.dayz-mcp*]`
    entries. The writer was most likely the Codex app; that was not observed.
  - The gate accepts only `command`, `args` and `tool_timeout_sec` there (`host_config.py:73`), so every DayZ-MCP
    client failed at startup from 20:55, in any session.
  - With the owner's approval the remaining line was removed; the TOML is otherwise equal. Inbox
    `fb-20261009-215009-ace5` asks for the durable fix.
  - Meanwhile a Codex session had reconnected and ran a DayZ_MCP server from 23:36 to 23:41 on the candidate tree.
- **SimpleGroup: PASS again.**
  - Same result: 70 entries, 62/62 byte-identical copies, `config.bin` and 6 ODOL models, the same argv shape.
  - The two new mutants give FAIL.
  - The server ran SimpleGroup's code, and the run was stopped through `dayz_test_stop`.
- **LFHeli_OH1: refused as specified.**
  - `dayz_test_run` returned `status=failed`, `error_code=build_namespace_source_mismatch`, `run_id=null`,
    `cleanup_degraded=false` and the remediation text of the spec, in 1.1 s.
  - The deployed PBO kept its hash (`194FD1A5`), read before any restore.
  - The argv sampler saw only the launcher (the private worker): no AddonBuilder, binarize, CfgConvert or FileBank.
    The AddonBuilder user log gained 0 bytes.
- **Close:**
  - #230 merged as `9f3e7de`. Its `tools/` is identical to `1380e0c`; only #231's docs differ.
  - LIVE moved to `main` and T130 detached on `9f3e7de`. The lock and registry checks pass on both: PE `CA9E262F` and
    `F2048F35`, `app.pyz` `9DCC15BA`.

## Not verified
- `-packonly` with a namespace that differs from the folder (allowed by design B) was not run with real Tools.
- Projects other than SimpleGroup and LFHeli_OH1 were not built.
- The models of the SimpleGroup build were checked as ODOL, not opened.
- The ODOL bytes differ between builds, a known AddonBuilder variation.
