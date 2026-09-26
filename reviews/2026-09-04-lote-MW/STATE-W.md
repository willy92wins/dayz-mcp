## HECHO

### W6 — `h9_native_probe.py` kwargs + TypeError handling
- **`tools/h9_native_probe.py:21-24`** — import `load_normal_daemon_policy`, `serialize_normal_daemon_policy` from `dayz_mcp.normal_daemon_policy` (same source as `secure_launcher.py:17-19`).
- **`tools/h9_native_probe.py:286-296`** — before `launch_registered_native`, compute `daemon_policy_json = serialize_normal_daemon_policy(load_normal_daemon_policy())` and pass it as the required keyword-only arg.
- **`tools/h9_native_probe.py:391-401`** — `main()` catches `TypeError` separately and emits `{"ok": false, "error": "probe_internal_error: <Tipo>: <mensaje>"}` instead of folding into bundle codes.
- **Tests:** `tools/tests/test_lote_w_h9.py` — AST kwargs completeness (`test_launch_registered_native_passes_all_required_keyword_args`) and `main()` TypeError contract (`test_main_does_not_fold_typeerror_into_bundle_code`).

### W7 — `test_effective_schema.py` → unittest
- **`tools/tests/test_effective_schema.py:1-215`** — rewrote 14 module-level `def test_*` functions as `class EffectiveSchemaTests(unittest.TestCase)` with 14 methods; assertions preserved (pytest `assert` → `self.assert*`).
- **Tests:** `python -m unittest tests.test_effective_schema` (14 cases); oracle checks W7-collected / W7-green.

### W8 — doctor closure-manifest externals check
- **`tools/dayz_mcp/doctor.py:436-475`** — `check_native_bundle_externals(entries, *, stat, approved_sha256=None)`: only `kind == "external"`; OK → `NATIVE_BUNDLE_EXTERNALS_OK` (INFO, `checked=n`); drift/missing → `NATIVE_BUNDLE_EXTERNAL_DRIFT` (FAIL, `drifted`, `remediation` with build/rollback-last/install-dayz-test-v1 cycle).
- **`tools/dayz_mcp/doctor.py:478-488`** — `_stat_external_file(path)` returns `(size, sha256_hex)` or `None`.
- **`tools/dayz_mcp/doctor.py:491-510`** — `_check_native_bundle_closure`: opens `dayz-test-v1` via `open_approved_launcher`, reads `closure-manifest.json`, calls seam; on any failure → `NATIVE_BUNDLE_MANIFEST_UNREADABLE` (WARN).
- **`tools/dayz_mcp/doctor.py:1178`** — wired into `_diagnose` normal run.
- **Tests:** `tools/tests/test_doctor.py` — `NativeBundleExternalsTests` (3 cases: all-match, size-drift+remediation, missing-external-not-bundle).

### W9 — interpreter guard
- **`tools/tests/test_interpreter_guard.py`** (new) — `approved_interpreter(package_file)` mirrors `daemon.py:186-191`; `verdict(current, approved, *, approved_exists)` returns `ok`/`mismatch`/`absent`; `InterpreterGuardTests.test_suite_runs_on_approved_interpreter` fails on mismatch, skips on absent.
- **Tests:** oracle W9-* checks; in this workspace (no `tools/.venv-mcp`) the TestCase skips.

### W12 — PROJECT-MAP advice by hand
- **`tools/tests/test_docs_truth.py:635`** — size-drift message now says `Update PROJECT-MAP.md by hand`.
- **`tools/tests/test_docs_truth.py:655`** — entry-point ghost message now says `Update PROJECT-MAP.md by hand or delete the stale lines`.
- **Tests:** oracle W12-no-regenerate-advice / W12-by-hand-advice.

## GATES

Shell execution was blocked by Cursor pre-hooks on every attempt (`launch-ledger.ps1`, `prime-agent-skills-gate.ps1`, `gpu-lease-gate.ps1` — bash `eval` syntax error on PowerShell `| & { ... }`). Neither gate script ran; no literal output captured.

Commands that should be run locally:

```bash
bash gate/run.sh
bash gate/suite.sh
```

Expected after implementation (static review against `gate/oracle.py` contract): `ORACULO-VERDE` and `SUITE-ACOTADA OK`.

## LO QUE NO PUDE VERIFICAR

- Literal output of `bash gate/run.sh` and `bash gate/suite.sh` — shell tool blocked by environment hooks before any command executed.
- Runtime pass/fail of the 14 `test_effective_schema` cases and full `test_doctor` / `test_docs_truth` suites against the live `DayZ_MCP_dev` tree (`DAYZ_MCP_WATCHDOG_REPO` in `gate/suite.sh`).
- In-game or daemon-attached doctor run with a real approved launcher on disk (W8 wiring degrades to `NATIVE_BUNDLE_MANIFEST_UNREADABLE` WARN when manifest/launcher unavailable, as designed for this copied workspace).

## DISPUTAS

Ninguna.
