# gpt-6.1-sol review, g5, round 2 (unedited)

VERDICT: CHANGES_REQUIRED

## FINDINGS

Paths below refer to the current tree. Reproductions used real functions, with process metadata or filesystem operations mocked where stated.

**F1 — P1 — Migration still accredits code that is not the executed target.**

Anchors: `tools/dayz_mcp/identity_migration.py:750`, `tools/dayz_mcp/identity_migration.py:726`, `tools/dayz_mcp/instance_context.py:76`.

`_script_target` treats the first absolute argument as the Python script, including server option values.

**Executable scenario:** a legacy writer hardcoded to `DayZ_MCP_130`, with cwd `C:\fixture\legacy`, has this argv:

```text
C:\Python314\python.exe -m dayz_mcp --daemon --keyfile C:\Users\guill\dzmcp_gauntlet\g5_instances\ws\tools\dayz_mcp\__main__.py
```

The executed target is the module imported from the legacy cwd. The scanner instead accredits this workspace’s package through the **keyfile argument**. Using that exact metadata and the real workspace marker, I observed disposition `not_writer` and scanner output `()`, while migrating `130`.

**Fix:** obtain the target from the actual Python invocation parser and verify its import resolution. Server argument values must never provide package accreditation. Previous F1 remains open.

---

**F2 — P1 — Lifetime exclusion permits independent writers within one process.**

Anchors: `tools/dayz_mcp/instance_context.py:165`, `tools/dayz_mcp/daemon.py:398`.

An existing root lease is automatically shared with every subsequent lease object in the same process. It does not distinguish independent runtime states, ports, keys or owners.

**Executable scenario:** process A already holds the `130` lease and serves state on 8775. In that process, call:

```python
daemon.build_server_state(
    SimpleNamespace(instance_token="130", port=8785,
                    game_path=None, enable_exec_enforce=False),
    "another-key",
    activate_coordination=True,
)
```

With the existing lease represented in the real lease registry and migration/activation mocked, I observed a second activation and holder count `2`. Independent coordination stores can therefore overwrite the same root.

**Fix:** reject independent ownership requests within the process too. Any reentrancy must belong to the same owner/state, rather than merely the same PID. Previous F2 is only partially fixed.

---

**F3 — P2 — Named registration transactions still use default-only parsers and timeout writers.**

Anchors: `tools/install_mcp.py:629`, `tools/install_mcp.py:746`, `tools/install_mcp.py:1185`, `tools/dayz_mcp/host_config.py:496`, `tools/dayz_mcp/host_config.py:517`.

**Executable scenarios:**

- Feed `parse_codex_registration` an otherwise valid Codex response whose `name` is `dayz-mcp-130`. **Observed:** `unsupported_codex_registration`. Consequently, the provider cannot verify or restore a named Codex registration.
- Feed the timeout builders valid files containing only `dayz-mcp-130`. **Observed:** `missing_claude_dayz_mcp` and `missing_codex_dayz_mcp`.
- If default and named registrations coexist, those builders update the default registration instead of the selected named one.

**Fix:** pass the registration name through parsing, transaction verification, timeout construction and rollback. Retain the shared recovery journal. Previous F6 remains incomplete.

---

**F4 — P2 — The installer invokes the migration gate without its selector.**

Anchors: `tools/install_mcp.py:1302`, `tools/install_mcp.py:1217`.

**Executable scenario:** register with `--instance 130 --port 8775`, with no instance environment variables. The captured gate argv ends with:

```text
p0s_gate.py backup-runs-v1 --port 8775
```

Neither instance nor game path is forwarded. `p0s_gate` consequently operates on the default store. With inherited named environment context, it instead rejects the installer’s omitted selector.

**Fix:** forward the validated selection into this subprocess and validate conflicts before installer writes. Adding flags to the gate’s parser alone does not close previous F7.

---

**F5 — P2 — Packaged builds cannot locate the shared build lock.**

Anchors: `tools/native-launchers/dayz-test-v1/src/launcher.cpp:621`, `tools/dayz_mcp/dayz_test_worker.py:785`, `tools/dayz_mcp/server_cli.py:80`.

The private worker’s minimal environment contains neither `LOCALAPPDATA` nor `DAYZ_MCP_SHARED_ROOT`.

**Executable scenario:** run a rebuilt launcher with `"build": true`. Before invoking `ADDON_BUILDER`, the worker enters `shared_build_lock`. Reproducing `shared_root()` under the environment constructed by `MinimalEnvironment` yields:

```text
OSError: localappdata_unavailable
```

This affects default and named builds.

**Fix:** provide the worker with an accredited, common lock location through the sealed launch boundary. Test the actual minimal environment.

---

**F6 — P1 — Builds sharing a destructive resource can acquire different locks.**

Anchors: `tools/dayz_mcp/server_cli.py:102`, `tools/dayz_mcp/dayz_test_worker.py:785`.

The lock hashes the complete `(target, temp)` tuple. Sharing either resource alone does not cause contention.

**Executable scenario:** concurrently request clean builds with:

```text
A: target=P:\Mods\@ExampleMod\Addons  temp=P:\temp\A\ExampleMod
B: target=P:\Mods\@ExampleMod\Addons  temp=P:\temp\B\ExampleMod
```

Executing the real lock-path construction with filesystem operations mocked produced **different lock files**. Both builds can therefore clear/write the same target concurrently. The symmetric case—different targets sharing a temp directory—also escapes exclusion.

**Fix:** lock each shared resource independently, acquiring multiple locks in a deterministic order. Previous F8 is partially fixed; the exact-pair regression does not cover overlapping resources.

---

**F7 — P2 — The PowerShell named installer still synchronizes global skills as default.**

Anchors: `tools/install-mcp.ps1:679`, `tools/install-mcp.ps1:683`, `tools/dayz_mcp/knowledge_pack.py:185`.

**Executable scenario:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/install-mcp.ps1 -Instance 130 -Port 8775 -Register
```

The knowledge child receives only:

```text
-m dayz_mcp.knowledge_pack install --sync
```

Its selection is therefore default. Reproducing that child context returned owner `default` and the default `DayZ_MCP\knowledge-pack` directory. A named installation can consequently synchronize global skills under default ownership.

**Fix:** propagate validated ownership into the child, and implement the named installation’s intended non-owner behavior. Previous F10 remains open at this entry point.

---

**F8 — P2 — Installer selector grammar remains inconsistent with startup and scanning.**

Anchors: `tools/install_mcp.py:808`, `tools/install_mcp.py:836`, `tools/install-mcp.ps1:747`.

**Executable inputs and observed results:**

```text
parse_args(["--instance=130", "--port", "8775"])
→ instance_token="130"

parse_args(["--instance", "130", "--instance", "other", "--port", "8775"])
→ instance_token="other"
```

Both inputs must be rejected under the binding specification. PowerShell also accepts tokens such as `a--b`, which the server validator rejects.

**Fix:** apply the shared selector grammar and duplicate/glued rejection before side effects across the required entry points.

---

**F9 — P2 — The upgrade invalidates an unchanged default sealed bundle.**

Anchors: `tools/dayz_mcp/dayz_test_worker.py:296`, `tools/dayz_mcp/native_bundle.py:1143`, `tools/dayz_mcp/native_bundle.py:1490`.

**Executable scenario:** retain a default bundle valid against the unmodified `base3` sources, then upgrade its tools tree to this implementation. I computed the four source pins from `base3` and passed them to the current validation functions.

**Observed:**

```text
source_pin_status → stale: dayz_test_worker.py / sha256_mismatch
_require_source_pins → invalid_native_launcher_bundle__dayz_test_worker_sha256
```

Thus the unchanged default bundle cannot remain accepted through this upgrade. This contradicts the explicit default executable compatibility requirement; rebuilding only the named launcher will not resolve it.

**Fix:** provide a verified compatibility strategy that preserves the unchanged default executable, or obtain a revised owner requirement before changing that contract.

---

**F10 — P2 — Normal-tier lifecycle tests still write the real shared profile.**

Anchors: `tools/dayz_mcp/server_cli.py:74`, `tools/dayz_mcp/server_cli.py:82`, `tools/dayz_mcp/box_admission.py:47`, `tools/tests/test_process_lifecycle.py:414`.

The isolation fallback applies only when `DAYZ_MCP_FAST_TESTS=1`. Existing lifecycle fixtures do not inject the shared root.

**Executable scenario:** with both shared-root and fast-tier variables unset, run:

```text
python -B -m unittest tests.test_process_lifecycle.ProcessLifecycleTest.test_diag_start_records_only_complete_strong_identity
```

Its `start_run` reaches box admission, which creates/opens:

```text
%LOCALAPPDATA%\DayZ_MCP_shared\box-admission.lock
```

The temporary manifest fixture does not redirect that path. This retains the pollution explicitly prohibited by the round-2 brief.

**Fix:** inject temporary shared roots into every affected fixture, including normal-tier execution, and test that mode.

## SPEC COVERAGE

| Specification bullet | Status |
|---|---|
| **1. Validated selector and default compatibility** | **Wrong:** server grammar is implemented, but installer grammar differs (F8). Default registration argv ordering and paths are retained; unchanged sealed executable compatibility fails (F9). Explicit server binding and environment-conflict checks are present; child propagation is incomplete (F5/F7). Registration `env` remains omitted. |
| **2. Spawn and authority propagation** | **Done:** config fields, daemon argv suffixes, provenance-derived spawn command, registration/token binding, host field comparison and captured policy revalidation selectors are present. Credential accreditation paths remain in place. **Wrong:** installation parsing/timeouts still lose named authority (F3). |
| **3. One writer per root** | **Wrong:** daemon and programmatic activation now acquire lifetime leases, but independent same-process writers are admitted (F2). |
| **4. Migration classification** | **Done:** valid-client exclusion, allowed-identity checks, recovery/post-copy selectors, settled-receipt shortcut and explicit gate selection are present. **Wrong:** executed-target accreditation remains bypassable (F1); installer gate invocation loses selection (F4). **Missing:** live validation of `130`’s unchanged existing receipt. |
| **5. Resource separation and shared recovery** | **Done:** named runtime/inbox/knowledge/capture/audit paths, named profile selection, named pre-write endpoint checks, dynamic registration commands and shared recovery journal. **Wrong:** named parsing/timeouts and PowerShell skill ownership (F3/F7). **Missing:** named `_mcp_config` template namespaces in both installers. |
| **6. Physical-box admission and independent build serialization** | **Done:** cross-process admission wraps lifecycle preparation, Steam work and spawn; leases remain instance-local. **Wrong:** packaged build-lock environment and overlapping-resource exclusion (F5/F6). **Missing:** simultaneous-launch and daemon-death acceptance evidence. |
| **7. Aligned sealed deployment** | **Done:** added packaged behavior remains within existing module inventories; lock metadata was updated; no PBO change is introduced. **Missing:** named rebuild/reseal and approved-launcher evidence. **Wrong:** unchanged default bundle acceptance (F9). **Missing:** deployed bridge acceptance. |

Previous-round findings: **F3, F5 and F9 are fixed at source level**; the others remain open or partially fixed as detailed above. The round-2 test-isolation correction is incomplete.

## GATE GAP

The supplied gate passes, but its new tests chiefly validate individual helpers:

- Explicit-script accreditation is tested; module invocation with an absolute server argument is not.
- Lease refusal is mocked; real same-process independent activation is not tested.
- Installer parsing is tested; named transaction verification, timeout writing and migration subprocess propagation are not.
- Worker profile selection receives a manually supplied environment; the actual native minimal environment is not exercised.
- Build contention uses identical target/temp pairs, rather than overlapping resources.
- Skills tests bind context directly, bypassing the PowerShell child.
- Default argv compatibility does not establish unchanged sealed-bundle compatibility.
- Isolation is tested under fast mode only.

There is no demonstrated barrier-controlled concurrent launch or rebuilt native deployment acceptance.

## PREMISE

The corrected edit boundary is sufficient. Shared-tree support and instance renaming remain out of scope; neither is needed to explain these blockers.

The supplied baseline gate failures are designated non-blocking, and I have not promoted them into findings. The old injected LIVE-STATE does not establish this batch’s deployed state.

## NOT VERIFIED

- Full focused suite in this sandbox: temporary-directory creation was denied. The system Python attempt also lacked dependencies.
- Using the existing project venv against this workspace, **nine read-only focused tests passed**. Additional in-memory reproductions produced the failures above.
- No native compilation, rebuilt binaries, live installer registration, deployed receipt/PBO checks or simultaneous DayZ launch was performed.
- No files were modified; no git/network operations, commit or written handoff were performed.